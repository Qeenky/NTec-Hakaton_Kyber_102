import 'dart:async';
import 'dart:math' as math;

import 'package:camera/camera.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/analytics.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/frame_result.dart';
import '../models/inspection_event.dart';
import '../models/labels.dart';
import '../models/telemetry.dart';
import '../models/vision.dart';
import '../services/api_service.dart';
import '../services/frame_codec.dart';
import '../services/frame_socket.dart';
import '../services/telemetry_socket.dart';
import '../theme/app_theme.dart';
import '../widgets/analytics_panel.dart';
import '../widgets/ar_overlay_painter.dart';
import '../widgets/diagnostic_card.dart';
import '../widgets/inspection_journal.dart';
import '../widgets/overlay_mapping.dart';
import '../widgets/vision_cards.dart';
import '../widgets/visual_signs.dart';
import 'component_detail_screen.dart';
import 'inspection_result_screen.dart';

/// AR-осмотр: превью камеры, оверлеи `/ws/frame`, OCR, диагностики и
/// живые значения `/ws/telemetry`. Стриминг ~4 кадра/с + резервный «Снять кадр».
class ArInspectionScreen extends StatefulWidget {
  const ArInspectionScreen({super.key, required this.equipment});

  final Equipment equipment;

  @override
  State<ArInspectionScreen> createState() => _ArInspectionScreenState();
}

class _ArInspectionScreenState extends State<ArInspectionScreen> {
  static const Duration _frameInterval = Duration(milliseconds: 250);

  CameraController? _camera;
  FrameSocket? _frameSocket;
  TelemetrySocket? _telemetrySocket;
  Listenable? _bottomListenable;
  ApiService? _api;
  final ImagePicker _photoPicker = ImagePicker();
  final GlobalKey _bottomBarKey = GlobalKey();

  bool _started = false;
  bool _cameraReady = false;
  bool _streaming = false;
  bool _capturing = false;
  bool _encoding = false;
  String? _cameraError;
  Uint8List? _frozenJpeg;

  bool _photoMode = false;
  bool _photoBusy = false;
  FrameResult? _photoFrame;
  String? _analysisError;
  double _bottomBarHeight = 0;

  bool _visionBusy = false;
  VisionCheck? _vision;
  String? _visionError;
  String? _visionComponentId;
  Uint8List? _lastFrameJpeg;

  final InspectionEventLog _journal = InspectionEventLog();
  int _lastProcessedFrameId = -1;
  bool? _lastBoardDetected;
  DateTime? _lastFreshBoardAt;
  String? _lastMarkerSignature;
  LinkState? _lastLinkState;
  String? _lastFatalError;
  Map<String, String> _lastDiagnostics = <String, String>{};
  Map<String, String> _lastSigns = <String, String>{};

  /// Один и тот же экземпляр виджета: родитель перестраивается на каждом
  /// кадре, а панель обновляется сама не чаще 2 раз в секунду.
  late final Widget _analyticsPanel;

  DateTime _lastSent = DateTime.fromMillisecondsSinceEpoch(0);
  int _shownSent = 0;
  int _fps = 0;
  Timer? _fpsTimer;

  FrameResult? get _currentFrame =>
      _photoMode ? _photoFrame : _frameSocket?.lastFrame;

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations(const [DeviceOrientation.portraitUp]);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    final app = AppScope.of(context);
    _api = app.api;
    final frameSocket = FrameSocket(
      baseUrl: app.settings.wsBaseUrl,
      equipmentId: widget.equipment.id,
    );
    frameSocket.connect();
    _frameSocket = frameSocket;
    frameSocket.addListener(_onFrameSocketChanged);
    _journal.add(
      InspectionEvent(
        timestamp: DateTime.now(),
        type: 'info',
        message: 'Осмотр начат: ${widget.equipment.id}',
      ),
    );
    _analyticsPanel = AnalyticsPanel(
      socket: frameSocket,
      roster: widget.equipment.components
          .map((component) => component.name)
          .toList(growable: false),
      initiallyExpanded: false,
    );

    final telemetrySocket = TelemetrySocket(
      baseUrl: app.settings.wsBaseUrl,
      equipmentId: widget.equipment.id,
    );
    telemetrySocket.connect();
    _telemetrySocket = telemetrySocket;
    _bottomListenable = Listenable.merge([frameSocket, telemetrySocket]);

    _fpsTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      final sent = _frameSocket?.sentFrames ?? 0;
      setState(() {
        _fps = sent - _shownSent;
        _shownSent = sent;
      });
    });
    _initCamera();
  }

  void _log(
    String type,
    String message, {
    String severity = 'info',
    int? frameId,
  }) {
    final current = _currentFrame?.frameId ?? 0;
    _journal.add(
      InspectionEvent(
        timestamp: DateTime.now(),
        frameId: frameId ?? (current > 0 ? current : null),
        type: type,
        message: message,
        severity: severity,
      ),
    );
  }

  void _onFrameSocketChanged() {
    final socket = _frameSocket;
    if (socket == null) return;

    final state = socket.state;
    if (_lastLinkState != state) {
      final previous = _lastLinkState;
      final wasLost =
          previous == LinkState.disconnected ||
          previous == LinkState.reconnecting ||
          previous == LinkState.noResponse;
      _lastLinkState = state;
      if (state == LinkState.live) {
        _log('link_restored', 'Связь с сервером установлена');
      } else if (!wasLost &&
          (state == LinkState.disconnected ||
              state == LinkState.reconnecting ||
              state == LinkState.noResponse)) {
        _log(
          'link_lost',
          state == LinkState.noResponse
              ? 'Сервер не отвечает дольше 3 секунд'
              : 'Связь с сервером потеряна',
          severity: 'warning',
        );
      }
    }

    final fatal = socket.fatalError;
    if (fatal != null && fatal != _lastFatalError) {
      _lastFatalError = fatal;
      _log('error', fatal, severity: 'critical');
    }

    final frame = socket.lastFrame;
    if (frame == null || frame.frameId == _lastProcessedFrameId) return;
    _lastProcessedFrameId = frame.frameId;
    _processFrameEvents(frame);
  }

  void _processFrameEvents(FrameResult frame) {
    final frameId = frame.frameId > 0 ? frame.frameId : null;
    final detected = frame.board.detected;
    if (detected && !frame.board.stale) {
      _lastFreshBoardAt = DateTime.now();
    }
    if (_lastBoardDetected != detected) {
      if (detected) {
        _log(
          'binding_restored',
          'Планшет найден: ${frame.board.markers} маркера',
          frameId: frameId,
        );
      } else {
        _log(
          'binding_lost',
          boardReasonLabel(frame.board.reason),
          severity: 'warning',
          frameId: frameId,
        );
      }
      _lastBoardDetected = detected;
    }
    if (detected) {
      final signature = frame.board.markerIds.join(',');
      if (signature.isNotEmpty &&
          _lastMarkerSignature != null &&
          signature != _lastMarkerSignature) {
        _log(
          'board_changed',
          'Сменился набор маркеров: $signature',
          frameId: frameId,
        );
      }
      if (signature.isNotEmpty) _lastMarkerSignature = signature;
    }

    final diagnostics = <String, String>{
      for (final event in frame.activeDiagnostics) event.ruleId: event.title,
    };
    for (final entry in diagnostics.entries) {
      if (!_lastDiagnostics.containsKey(entry.key)) {
        final event = frame.activeDiagnostics.firstWhere(
          (item) => item.ruleId == entry.key,
          orElse: () => frame.activeDiagnostics.first,
        );
        _log(
          'diagnostic_triggered',
          'Сработала диагностика: ${entry.value}',
          severity: event.severity,
          frameId: frameId,
        );
      }
    }
    for (final entry in _lastDiagnostics.entries) {
      if (!diagnostics.containsKey(entry.key)) {
        _log(
          'diagnostic_cleared',
          'Диагностика снята: ${entry.value}',
          frameId: frameId,
        );
      }
    }
    _lastDiagnostics = diagnostics;

    final signs = <String, String>{};
    final severities = <String, String>{};
    final window = frame.analytics;
    if (window != null) {
      for (final component in window.components) {
        for (final sign in component.signs) {
          if (!sign.isShown) continue;
          final key = '${component.componentId}:${sign.sign}';
          signs[key] = '${sign.label} (${component.label})';
          severities[key] = sign.isDetected ? 'warning' : 'info';
        }
      }
    }
    for (final entry in signs.entries) {
      if (!_lastSigns.containsKey(entry.key)) {
        _log(
          'sign_appeared',
          'Появился признак: ${entry.value}',
          severity: severities[entry.key] ?? 'info',
          frameId: frameId,
        );
      }
    }
    for (final entry in _lastSigns.entries) {
      if (!signs.containsKey(entry.key)) {
        _log(
          'sign_disappeared',
          'Признак больше не виден: ${entry.value}',
          frameId: frameId,
        );
      }
    }
    _lastSigns = signs;
  }

  void _openJournal() {
    _journal.flushNow();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (_) => InspectionJournalSheet(log: _journal),
    );
  }

  Future<void> _initCamera() async {
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        throw CameraException('no_camera', 'Камера не найдена');
      }
      final back = cameras.firstWhere(
        (camera) => camera.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );
      final controller = CameraController(
        back,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.yuv420,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() {
        _camera = controller;
        _cameraReady = true;
      });
      await _startStreaming();
    } on CameraException catch (exception) {
      if (!mounted) return;
      final message =
          'Камера недоступна: ${exception.description ?? exception.code}';
      setState(() => _cameraError = message);
      _log('error', message, severity: 'critical');
    } catch (exception) {
      if (!mounted) return;
      final message = 'Камера недоступна: $exception';
      setState(() => _cameraError = message);
      _log('error', message, severity: 'critical');
    }
  }

  Future<void> _startStreaming() async {
    final controller = _camera;
    if (controller == null) return;
    try {
      await controller.startImageStream(_onCameraFrame);
      if (!mounted) return;
      setState(() {
        _streaming = true;
      });
    } catch (exception) {
      if (!mounted) return;
      const message =
          'Поток кадров недоступен. Снимайте отдельные кадры кнопкой «Снять кадр».';
      setState(() {
        _streaming = false;
      });
      _log('error', message, severity: 'warning');
    }
  }

  Future<void> _stopStreaming() async {
    final controller = _camera;
    if (controller == null || !_streaming) return;
    try {
      await controller.stopImageStream();
    } catch (_) {}
    if (mounted) setState(() => _streaming = false);
  }

  Future<void> _onCameraFrame(CameraImage image) async {
    if (!mounted || _frozenJpeg != null || _encoding) return;
    final socket = _frameSocket;
    if (socket == null) return;
    final now = DateTime.now();
    if (now.difference(_lastSent) < _frameInterval) return;
    _lastSent = now;
    _encoding = true;
    try {
      final rotation = _camera?.description.sensorOrientation ?? 0;
      final controller = _camera;
      final targetAspect = controller == null
          ? null
          : _cameraPreviewAspect(controller);
      final jpeg = await encodeCameraFrame(
        image,
        rotationDegrees: rotation,
        targetAspect: targetAspect,
      );
      if (jpeg != null && mounted) {
        _lastFrameJpeg = jpeg;
        socket.send(jpeg);
      }
    } finally {
      _encoding = false;
    }
  }

  Future<void> _captureStill() async {
    final controller = _camera;
    if (controller == null || _capturing) {
      const message = 'Камера недоступна';
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
      return;
    }
    setState(() => _capturing = true);
    try {
      await _stopStreaming();
      final file = await controller.takePicture();
      final raw = await file.readAsBytes();
      final jpeg = await normalizeCapturedJpeg(raw);
      if (!mounted) return;
      setState(() {
        _frozenJpeg = jpeg;
        _photoMode = false;
        _photoFrame = null;
        _analysisError = null;
        _vision = null;
        _visionError = null;
      });
      _lastFrameJpeg = jpeg;
      _frameSocket?.send(jpeg);
    } on CameraException catch (exception) {
      final message =
          'Не удалось снять кадр: ${exception.description ?? exception.code}';
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } catch (_) {
      const message = 'Не удалось снять кадр';
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } finally {
      if (mounted) setState(() => _capturing = false);
    }
  }

  Future<void> _pickPhoto() async {
    if (_photoBusy || _capturing) return;
    XFile? picked;
    try {
      picked = await _photoPicker.pickImage(source: ImageSource.gallery);
    } catch (exception) {
      final message = 'Не удалось открыть галерею: $exception';
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
      return;
    }
    if (picked == null || !mounted) return;

    setState(() => _photoBusy = true);
    try {
      final raw = await picked.readAsBytes();
      final jpeg = await prepareUploadedPhoto(raw);
      if (!mounted) return;
      await _stopStreaming();
      if (!mounted) return;
      setState(() {
        _frozenJpeg = jpeg;
        _photoMode = true;
        _photoFrame = null;
        _analysisError = null;
        _vision = null;
        _visionError = null;
      });
      _lastFrameJpeg = jpeg;
      await _analyzePhoto(jpeg);
    } on FormatException {
      const message =
          'Не удалось распознать изображение. Выберите JPEG или PNG.';
      if (mounted) setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } catch (_) {
      const message = 'Не удалось прочитать изображение';
      if (mounted) setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } finally {
      if (mounted) setState(() => _photoBusy = false);
    }
  }

  Future<void> _analyzePhoto(Uint8List jpeg) async {
    final api = _api;
    if (api == null) return;
    setState(() {
      _photoBusy = true;
      _analysisError = null;
    });
    try {
      final frame = await api.analyzePhoto(
        equipmentId: widget.equipment.id,
        imageBytes: jpeg,
      );
      if (!mounted) return;
      setState(() {
        _photoFrame = frame;
        _analysisError = frame.error == null
            ? null
            : (frame.error == 'invalid_image'
                  ? 'Сервер не смог разобрать изображение'
                  : 'Сервер вернул ошибку: ${frame.error}');
        if (frame.vision != null) {
          _vision = frame.vision;
          _visionComponentId = frame.vision!.componentId;
          _visionError = null;
        }
      });
      _processFrameEvents(frame);
      _log(
        'photo_analyzed',
        'Фото обработано: ${frame.overlays.length} зон',
        frameId: frame.frameId > 0 ? frame.frameId : null,
      );
      if (frame.error != null) {
        _log(
          'error',
          'Ошибка анализа фото: ${frame.error}',
          severity: 'warning',
          frameId: frame.frameId > 0 ? frame.frameId : null,
        );
      }
    } on ApiException catch (exception) {
      if (!mounted) return;
      final message = _analyzeErrorText(exception);
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } catch (_) {
      if (!mounted) return;
      const message = 'Не удалось проанализировать фото';
      setState(() => _analysisError = message);
      _log('error', message, severity: 'warning');
    } finally {
      if (mounted) setState(() => _photoBusy = false);
    }
  }

  String _analyzeErrorText(ApiException exception) {
    switch (exception.statusCode) {
      case 404:
        return 'Сервер не поддерживает загрузку фото: /analyze не найден. '
            'Обновите backend.';
      case 413:
        return exception.message.contains('HTTP 413')
            ? 'Фото слишком большое для сервера. Выберите снимок меньше.'
            : exception.message;
      case 415:
        return 'Сервер не поддерживает формат изображения.';
      case 422:
        return exception.message.contains('HTTP 422')
            ? 'Сервер не смог обработать фото (422).'
            : exception.message;
      default:
        return exception.message;
    }
  }

  Future<void> _resumeStream() async {
    setState(() {
      _frozenJpeg = null;
      _photoMode = false;
      _photoFrame = null;
      _analysisError = null;
      _vision = null;
      _visionError = null;
    });
    await _startStreaming();
  }

  List<OverlayZone> _visibleZones(FrameResult? frame) {
    if (frame == null) return const <OverlayZone>[];
    return frame.overlays
        .where((zone) => zone.visible && zone.hasGeometry)
        .toList(growable: false);
  }

  String? _selectedComponentId(FrameResult? frame) {
    final zones = _visibleZones(frame);
    if (zones.isEmpty) return null;
    final selected = _visionComponentId;
    if (selected != null && zones.any((zone) => zone.componentId == selected)) {
      return selected;
    }
    return zones.first.componentId;
  }

  void _selectVisionComponent(String componentId) {
    setState(() {
      _visionComponentId = componentId;
      if (_vision != null && _vision!.componentId != componentId) {
        _vision = null;
        _visionError = null;
      }
    });
  }

  Future<void> _runVisionCheck() async {
    if (_visionBusy) return;
    final frame = _currentFrame;
    final componentId = _selectedComponentId(frame);
    final jpeg = _lastFrameJpeg ?? _frozenJpeg;
    if (componentId == null) {
      const message = 'В кадре нет видимого узла для AI-проверки';
      setState(() => _visionError = message);
      _log('error', message, severity: 'warning');
      return;
    }
    if (jpeg == null) {
      const message = 'Нет кадра. Снимите кадр или дождитесь потока с камеры.';
      setState(() => _visionError = message);
      _log('error', message, severity: 'warning');
      return;
    }
    final api = _api;
    if (api == null) return;

    setState(() {
      _visionBusy = true;
      _visionError = null;
      _visionComponentId = componentId;
    });
    try {
      final check = await api.visionCheck(
        equipmentId: widget.equipment.id,
        componentId: componentId,
        imageBytes: jpeg,
      );
      if (!mounted) return;
      setState(() {
        _vision = check;
        _visionBusy = false;
      });
      _log(
        'ai_check',
        'AI-проверка «${componentLabel(componentId)}»: '
            '${check.hasError ? 'ошибка модели' : visionVerdictLabel(check.verdict)}',
        severity: check.isDefect
            ? 'critical'
            : (check.hasError ? 'warning' : 'info'),
      );
    } on ApiException catch (exception) {
      if (!mounted) return;
      final message = _visionRequestError(exception);
      setState(() => _visionError = message);
      _log('error', 'AI-проверка: $message', severity: 'warning');
    } catch (_) {
      if (!mounted) return;
      const message = 'Не удалось выполнить AI-проверку';
      setState(() => _visionError = message);
      _log('error', message, severity: 'warning');
    } finally {
      if (mounted) setState(() => _visionBusy = false);
    }
  }

  String _visionRequestError(ApiException exception) {
    switch (exception.statusCode) {
      case 404:
        return 'Агрегат не найден на сервере';
      case 413:
        return exception.message;
      case 422:
        return exception.message.contains('HTTP 422')
            ? 'Сервер не принял кадр для AI-проверки'
            : exception.message;
      case 503:
        return 'Локальная модель недоступна. Проверьте Ollama на сервере.';
      default:
        return exception.message;
    }
  }

  Future<void> _finish() async {
    await _stopStreaming();
    if (!mounted) return;
    _journal.add(
      InspectionEvent(
        timestamp: DateTime.now(),
        type: 'info',
        message: 'Осмотр завершён, открыт результат',
      ),
    );
    _journal.flushNow();
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => InspectionResultScreen(
          equipment: widget.equipment,
          frame: _currentFrame,
          telemetry: _telemetrySocket?.latest,
          frozenJpeg: _frozenJpeg,
          events: _journal.events,
        ),
      ),
    );
    if (!mounted) return;
    if (saved == true) {
      Navigator.of(context).pop(true);
    } else if (_frozenJpeg == null) {
      await _startStreaming();
    }
  }

  void _scheduleBottomMeasure() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final box = _bottomBarKey.currentContext?.findRenderObject();
      if (box is! RenderBox || !box.hasSize) return;
      final height = box.size.height;
      if ((height - _bottomBarHeight).abs() > 1) {
        setState(() => _bottomBarHeight = height);
      }
    });
  }

  void _handleOverlayTap(Offset local, Size size) {
    final frame = _currentFrame;
    if (frame == null || !frame.hasFrameSize) return;
    if (!frame.board.detected || frame.board.stale || _staleTooLong) return;
    if (local.dy > size.height - _bottomBarHeight) return;

    final viewport = _photoMode
        ? Size(size.width, math.max(0.0, size.height - _bottomBarHeight))
        : size;
    final mapping = OverlayMapping.forFit(
      viewport: viewport,
      frame: Size(frame.imageWidth.toDouble(), frame.imageHeight.toDouble()),
      fit: _photoMode ? BoxFit.contain : BoxFit.cover,
    );
    final point = mapping.toFrame(local);
    for (final zone in frame.drawableOverlays().reversed) {
      if (!_pointInPolygon(point, zone.polygon)) continue;
      ComponentInfo? component;
      for (final item in widget.equipment.components) {
        if (item.name == zone.componentId) {
          component = item;
          break;
        }
      }
      if (component == null) {
        final message = 'Узел «${zone.label}» отсутствует в карточке агрегата';
        setState(() => _analysisError = message);
        _log('error', message, severity: 'warning');
        return;
      }
      AnalyticsComponent? analyticsComponent;
      final window = frame.analytics;
      if (window != null) {
        for (final item in window.components) {
          if (item.componentId == zone.componentId) {
            analyticsComponent = item;
            break;
          }
        }
      }
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ComponentDetailScreen(
            equipment: widget.equipment,
            component: component!,
            diagnostics: frame.activeDiagnostics,
            visible: true,
            analyticsComponent: analyticsComponent,
          ),
        ),
      );
      return;
    }
  }

  bool _pointInPolygon(Offset point, List<List<double>> polygon) {
    var inside = false;
    for (var i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      final xi = polygon[i][0], yi = polygon[i][1];
      final xj = polygon[j][0], yj = polygon[j][1];
      final intersect =
          ((yi > point.dy) != (yj > point.dy)) &&
          (point.dx <
              (xj - xi) *
                      (point.dy - yi) /
                      ((yj - yi) == 0 ? 1e-9 : (yj - yi)) +
                  xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  @override
  void dispose() {
    _fpsTimer?.cancel();
    _frameSocket?.removeListener(_onFrameSocketChanged);
    _frameSocket?.dispose();
    _telemetrySocket?.dispose();
    _journal.dispose();
    final controller = _camera;
    _camera = null;
    if (controller != null) {
      () async {
        try {
          await controller.stopImageStream();
        } catch (_) {}
        await controller.dispose();
      }();
    }
    SystemChrome.setPreferredOrientations(const []);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Осмотр идёт поверх камеры: в светлой теме текст кнопок и плашек
    // становится тёмным на тёмном фоне. AR-экран всегда тёмный.
    return Theme(
      data: buildAppTheme(brightness: Brightness.dark),
      child: Builder(builder: _buildScreen),
    );
  }

  Widget _buildScreen(BuildContext context) {
    _scheduleBottomMeasure();
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          _buildPreview(),
          Positioned.fill(
            child: IgnorePointer(
              child: AnimatedBuilder(
                animation:
                    _frameSocket ?? const AlwaysStoppedAnimation<double>(0),
                builder: (context, _) => CustomPaint(
                  painter: ArOverlayPainter(
                    frame: _currentFrame,
                    clipBottom: _bottomBarHeight,
                    fit: _photoMode ? BoxFit.contain : BoxFit.cover,
                    hideOverlays: _staleTooLong,
                  ),
                ),
              ),
            ),
          ),
          Positioned.fill(
            child: LayoutBuilder(
              builder: (context, constraints) => GestureDetector(
                behavior: HitTestBehavior.translucent,
                onTapUp: (details) => _handleOverlayTap(
                  details.localPosition,
                  constraints.biggest,
                ),
              ),
            ),
          ),
          if (_photoBusy)
            Positioned.fill(
              child: IgnorePointer(
                child: ColoredBox(
                  color: Colors.black.withValues(alpha: 0.35),
                  child: const Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        CircularProgressIndicator(),
                        SizedBox(height: 12),
                        Text(
                          'Анализируем фото на сервере…',
                          style: TextStyle(fontSize: 13.5),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          if (_visionBusy)
            Positioned.fill(
              child: IgnorePointer(
                child: ColoredBox(
                  color: Colors.black.withValues(alpha: 0.42),
                  child: const Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        CircularProgressIndicator(),
                        SizedBox(height: 12),
                        Text(
                          'Модель смотрит кадр…',
                          style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        SizedBox(height: 4),
                        Text(
                          'Обычно 2–4 секунды',
                          style: TextStyle(
                            color: AppColors.textMuted,
                            fontSize: 11.5,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          Positioned.fill(
            child: AnimatedBuilder(
              animation:
                  _frameSocket ?? const AlwaysStoppedAnimation<double>(0),
              builder: (context, _) => _buildTopChrome(),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: AnimatedBuilder(
              animation:
                  _bottomListenable ?? const AlwaysStoppedAnimation<double>(0),
              builder: (context, _) => _buildBottomChrome(),
            ),
          ),
        ],
      ),
    );
  }

  ButtonStyle _compactButtonStyle() => OutlinedButton.styleFrom(
    minimumSize: const Size.fromHeight(46),
    padding: const EdgeInsets.symmetric(horizontal: 4),
    backgroundColor: Colors.black.withValues(alpha: 0.45),
    // Кнопки висят над камерой: подпись всегда светлая, независимо от темы.
    foregroundColor: Colors.white,
    disabledForegroundColor: Colors.white70,
    side: const BorderSide(color: Colors.white24, width: 1),
    textStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
  );

  Widget _compactLabel(String text) => FittedBox(
    fit: BoxFit.scaleDown,
    child: Text(
      text,
      maxLines: 1,
      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
    ),
  );

  Widget _buildPreview() {
    final controller = _camera;
    if (_frozenJpeg != null) {
      final image = Image.memory(
        _frozenJpeg!,
        fit: _photoMode ? BoxFit.contain : BoxFit.cover,
        gaplessPlayback: true,
      );
      if (_photoMode) {
        return Padding(
          padding: EdgeInsets.only(bottom: _bottomBarHeight),
          child: ColoredBox(
            color: Colors.black,
            child: SizedBox.expand(child: image),
          ),
        );
      }
      return SizedBox.expand(child: image);
    }
    if (controller != null && _cameraReady) {
      return _CoverCameraPreview(controller: controller);
    }
    return ColoredBox(
      color: Colors.black,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                _cameraError == null
                    ? Icons.photo_camera_outlined
                    : Icons.videocam_off_outlined,
                size: 54,
                color: AppColors.textMuted,
              ),
              const SizedBox(height: 14),
              Text(
                _cameraError ?? 'Запуск камеры…',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 14,
                  height: 1.4,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Осмотр можно завершить и без кадров: результат сохранится '
                'с текущей диагностикой сервера.',
                textAlign: TextAlign.center,
                style: TextStyle(color: AppColors.textMuted, fontSize: 12),
              ),
            ],
          ),
        ),
      ),
    );
  }

  bool get _staleTooLong {
    final frame = _currentFrame;
    if (frame == null || !frame.board.detected || !frame.board.stale) {
      return false;
    }
    final fresh = _lastFreshBoardAt;
    if (fresh == null) return false;
    return DateTime.now().difference(fresh) >
        const Duration(milliseconds: 1500);
  }

  Widget _buildTopChrome() {
    final socket = _frameSocket;
    final frame = _currentFrame;
    final linkState = socket?.state ?? LinkState.connecting;
    final linkColor = switch (linkState) {
      LinkState.live => AppColors.green,
      LinkState.connecting => AppColors.accent,
      LinkState.reconnecting || LinkState.noResponse => AppColors.yellow,
      LinkState.disconnected => AppColors.red,
    };
    final frameText = frame == null
        ? 'кадр —'
        : (frame.frameId > 0 ? 'кадр ${frame.frameId}' : 'фото');
    final bindingLost =
        frame != null && (!frame.board.detected || _staleTooLong);
    final bindingWeak = frame != null && frame.board.stale && !bindingLost;
    final statusText = bindingLost
        ? '$frameText · привязка потеряна'
        : bindingWeak
        ? '$frameText · привязка ослаблена'
        : '$frameText · $_fps к/с';
    final visibleText = frame == null || bindingLost
        ? 'видно —'
        : 'видно ${frame.visibleOverlayCount} из ${frame.totalOverlayCount}';

    return SafeArea(
      bottom: false,
      child: Align(
        alignment: Alignment.topCenter,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 6, 8, 0),
          child: Row(
            children: [
              _RoundButton(
                icon: Icons.arrow_back,
                compact: true,
                onTap: () => Navigator.of(context).pop(false),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Container(
                  height: 28,
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.45),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      Semantics(
                        label: 'Связь: ${linkState.label}',
                        child: Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: linkColor,
                            shape: BoxShape.circle,
                          ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Flexible(
                        child: Text(
                          statusText,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: bindingLost || bindingWeak
                                ? AppColors.yellow
                                : AppColors.text,
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          visibleText,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: AppColors.textMuted,
                            fontSize: 11,
                          ),
                        ),
                      ),
                      const Spacer(),
                      AnimatedBuilder(
                        animation: _journal,
                        builder: (context, _) => _JournalButton(
                          count: _journal.length,
                          onTap: _openJournal,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildBottomChrome() {
    final telemetry = _telemetrySocket?.latest;
    final frame = _currentFrame;
    final zones = _visibleZones(frame);
    final selected = _selectedComponentId(frame);
    return Container(
      key: _bottomBarKey,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Colors.transparent,
            Colors.black.withValues(alpha: 0.55),
            Colors.black.withValues(alpha: 0.92),
          ],
        ),
      ),
      padding: const EdgeInsets.fromLTRB(12, 28, 12, 10),
      child: SafeArea(
        top: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_vision != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _VisionSummaryBar(
                  check: _vision!,
                  onTap: () {},
                  onClose: () => setState(() => _vision = null),
                ),
              ),
            if (_visionError != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Container(
                  padding: const EdgeInsets.fromLTRB(10, 6, 6, 6),
                  decoration: BoxDecoration(
                    color: AppColors.orange.withValues(alpha: 0.16),
                    borderRadius: BorderRadius.circular(9),
                    border: Border.all(
                      color: AppColors.orange.withValues(alpha: 0.65),
                    ),
                  ),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.auto_awesome,
                        size: 16,
                        color: AppColors.orange,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'AI-проверка: $_visionError',
                          style: const TextStyle(
                            fontSize: 11.5,
                            height: 1.3,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      TextButton(
                        onPressed: _visionBusy ? null : _runVisionCheck,
                        child: const Text('Повторить'),
                      ),
                    ],
                  ),
                ),
              ),
            if (_vision != null || _visionError != null)
              const SizedBox(height: 2),
            if (frame != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _FindingsPanel(
                  diagnostics: frame.activeDiagnostics,
                  components:
                      frame.analytics?.components ??
                      const <AnalyticsComponent>[],
                ),
              ),
            if (_analysisError != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Container(
                  padding: const EdgeInsets.fromLTRB(10, 6, 6, 6),
                  decoration: BoxDecoration(
                    color: AppColors.red.withValues(alpha: 0.16),
                    borderRadius: BorderRadius.circular(9),
                    border: Border.all(
                      color: AppColors.red.withValues(alpha: 0.65),
                    ),
                  ),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.error_outline,
                        size: 16,
                        color: AppColors.red,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _analysisError!,
                          style: const TextStyle(
                            fontSize: 11.5,
                            height: 1.3,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      if (_frozenJpeg != null && _photoMode)
                        TextButton(
                          onPressed: _photoBusy
                              ? null
                              : () => _analyzePhoto(_frozenJpeg!),
                          child: const Text('Повторить'),
                        ),
                    ],
                  ),
                ),
              ),
            if (frame?.ocr != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _OcrStrip(ocr: frame!.ocr!),
              ),
            if (zones.length > 1) ...[
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'УЗЕЛ ДЛЯ AI-ПРОВЕРКИ',
                      style: TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.6,
                      ),
                    ),
                    const SizedBox(height: 4),
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          for (final zone in zones)
                            Padding(
                              padding: const EdgeInsets.only(right: 6),
                              child: ChoiceChip(
                                label: Text(
                                  zone.label.isEmpty
                                      ? componentLabel(zone.componentId)
                                      : zone.label,
                                  style: TextStyle(
                                    fontSize: 11,
                                    fontWeight: FontWeight.w600,
                                    color: selected == zone.componentId
                                        ? AppColors.primary
                                        : AppColors.text,
                                  ),
                                ),
                                selected: selected == zone.componentId,
                                visualDensity: VisualDensity.compact,
                                materialTapTargetSize:
                                    MaterialTapTargetSize.shrinkWrap,
                                selectedColor: AppColors.primary.withValues(
                                  alpha: 0.2,
                                ),
                                backgroundColor: Colors.black.withValues(
                                  alpha: 0.45,
                                ),
                                side: BorderSide(
                                  color: selected == zone.componentId
                                      ? AppColors.primary
                                      : AppColors.outline,
                                ),
                                onSelected: (_) =>
                                    _selectVisionComponent(zone.componentId),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
            _LiveTelemetryBar(
              telemetry: telemetry,
              state: _telemetrySocket?.state,
            ),
            if (_frameSocket != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: _analyticsPanel,
              ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    key: const Key('capture-frame-button'),
                    onPressed: (_capturing || _photoBusy || _visionBusy)
                        ? null
                        : (_frozenJpeg != null ? _resumeStream : _captureStill),
                    style: _compactButtonStyle(),
                    child: _capturing
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : _compactLabel(
                            _frozenJpeg != null ? 'К потоку' : 'Снять кадр',
                          ),
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: OutlinedButton(
                    key: const Key('pick-photo-button'),
                    onPressed: (_photoBusy || _capturing || _visionBusy)
                        ? null
                        : _pickPhoto,
                    style: _compactButtonStyle(),
                    child: _photoBusy
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : _compactLabel('Загрузить фото'),
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: OutlinedButton(
                    key: const Key('vision-check-button'),
                    onPressed: (_visionBusy || _photoBusy || _capturing)
                        ? null
                        : _runVisionCheck,
                    style: _compactButtonStyle(),
                    child: _visionBusy
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : _compactLabel('AI-проверка'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              key: const Key('finish-inspection-button'),
              onPressed: (_visionBusy || _photoBusy) ? null : _finish,
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text('Завершить осмотр'),
            ),
            if (_frozenJpeg != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    Icon(
                      _photoMode
                          ? Icons.photo_outlined
                          : Icons.pause_circle_outline,
                      size: 15,
                      color: _photoMode ? AppColors.accent : AppColors.yellow,
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        _photoMode
                            ? (_photoBusy
                                  ? 'Анализируем загруженное фото…'
                                  : (_photoFrame == null
                                        ? 'Фото не проанализировано · нажмите «Повторить»'
                                        : 'Загруженное фото · оверлеи по ответу /analyze'))
                            : 'Замороженный кадр · оверлеи по последнему ответу',
                        style: const TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 11.5,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

double _cameraPreviewAspect(CameraController controller) {
  final value = controller.value;
  final orientation = value.deviceOrientation;
  final landscape =
      orientation == DeviceOrientation.landscapeLeft ||
      orientation == DeviceOrientation.landscapeRight;
  return landscape ? value.aspectRatio : 1 / value.aspectRatio;
}

class _CoverCameraPreview extends StatelessWidget {
  const _CoverCameraPreview({required this.controller});

  final CameraController controller;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = constraints.biggest;
        final previewAspect = _cameraPreviewAspect(controller);
        final widthFromHeight = size.height * previewAspect;
        final double width;
        final double height;
        if (widthFromHeight >= size.width) {
          width = widthFromHeight;
          height = size.height;
        } else {
          width = size.width;
          height = size.width / previewAspect;
        }
        return ClipRect(
          child: OverflowBox(
            maxWidth: width,
            maxHeight: height,
            minWidth: width,
            minHeight: height,
            child: SizedBox(
              width: width,
              height: height,
              child: CameraPreview(controller),
            ),
          ),
        );
      },
    );
  }
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({
    required this.icon,
    required this.onTap,
    this.compact = false,
  });

  final IconData icon;
  final VoidCallback onTap;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black.withValues(alpha: 0.45),
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onTap,
        child: Padding(
          padding: EdgeInsets.all(compact ? 5 : 9),
          child: Icon(icon, size: compact ? 18 : 22),
        ),
      ),
    );
  }
}

class _JournalButton extends StatelessWidget {
  const _JournalButton({required this.count, required this.onTap});

  final int count;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: SizedBox(
        width: 32,
        height: 26,
        child: Stack(
          clipBehavior: Clip.none,
          alignment: Alignment.center,
          children: [
            const Icon(Icons.receipt_long_outlined, size: 17),
            if (count > 0)
              Positioned(
                right: 0,
                top: -1,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 3,
                    vertical: 1,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.accent,
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    count > 99 ? '99+' : '$count',
                    style: const TextStyle(
                      color: AppColors.onPrimary,
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      height: 1.0,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _OcrStrip extends StatelessWidget {
  const _OcrStrip({required this.ocr});

  final OcrReading ocr;

  @override
  Widget build(BuildContext context) {
    final stale = ocr.stale;
    final hasValue = ocr.value != null;
    final color = stale
        ? AppColors.grey
        : (hasValue ? AppColors.primary : AppColors.red);
    return Opacity(
      opacity: stale ? 0.6 : 1,
      child: Container(
        height: 28,
        padding: const EdgeInsets.symmetric(horizontal: 9),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.55),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.45)),
        ),
        child: Row(
          children: [
            const Icon(Icons.speed, color: AppColors.textMuted, size: 13),
            const SizedBox(width: 6),
            const Text(
              'МАНОМЕТР',
              style: TextStyle(
                color: AppColors.textMuted,
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.5,
              ),
            ),
            const SizedBox(width: 8),
            Text(
              ocr.displayValue,
              style: TextStyle(
                color: color,
                fontSize: 16,
                fontWeight: FontWeight.w800,
                height: 1.0,
              ),
            ),
            const SizedBox(width: 3),
            Text(
              ocr.unit,
              style: const TextStyle(color: AppColors.textMuted, fontSize: 11),
            ),
            const Spacer(),
            Text(
              hasValue
                  ? (stale
                        ? 'последнее · ${(ocr.confidence * 100).round()}%'
                        : '${(ocr.confidence * 100).round()}%'
                              '${ocr.ageFrames > 0 ? ' · ${ocr.ageFrames} кадр.' : ''}')
                  : ocrErrorLabel(ocr.error),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: AppColors.textMuted, fontSize: 11),
            ),
          ],
        ),
      ),
    );
  }
}

class _FindingsPanel extends StatefulWidget {
  const _FindingsPanel({required this.diagnostics, required this.components});

  final List<DiagnosticEvent> diagnostics;
  final List<AnalyticsComponent> components;

  @override
  State<_FindingsPanel> createState() => _FindingsPanelState();
}

class _FindingsPanelState extends State<_FindingsPanel> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final signs = widget.components
        .expand((component) => component.signs)
        .where((sign) => sign.isShown)
        .toList(growable: false);
    if (widget.diagnostics.isEmpty && signs.isEmpty) {
      return const SizedBox.shrink();
    }
    return Container(
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.outline),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: BorderRadius.circular(10),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(10, 7, 8, 7),
              child: Row(
                children: [
                  Icon(
                    widget.diagnostics.isEmpty
                        ? Icons.visibility_outlined
                        : Icons.warning_amber_rounded,
                    size: 15,
                    color: widget.diagnostics.isEmpty
                        ? AppColors.orange
                        : AppColors.yellow,
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: Text(
                      'Диагностик: ${widget.diagnostics.length} · '
                      'Признаков: ${signs.length}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  Icon(
                    _expanded ? Icons.expand_less : Icons.expand_more,
                    size: 18,
                    color: AppColors.textMuted,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final event in widget.diagnostics)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: DiagnosticCard(
                        event: event,
                        compact: true,
                        showEvidence: false,
                      ),
                    ),
                  if (signs.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    const Text(
                      'ВИЗУАЛЬНЫЕ ПРИЗНАКИ',
                      style: TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.6,
                      ),
                    ),
                    const SizedBox(height: 4),
                    for (final component in widget.components)
                      if (component.signs.any((sign) => sign.isShown))
                        Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                component.label,
                                style: const TextStyle(
                                  fontSize: 11,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              VisualSignsInline(signs: component.signs),
                            ],
                          ),
                        ),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _VisionSummaryBar extends StatelessWidget {
  const _VisionSummaryBar({
    required this.check,
    required this.onTap,
    required this.onClose,
  });

  final VisionCheck check;
  final VoidCallback onTap;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final color = visionVerdictColor(check);
    final title = check.hasError
        ? 'Модель не смогла проверить кадр'
        : visionVerdictLabel(check.verdict);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(11),
        child: Container(
          padding: const EdgeInsets.fromLTRB(10, 8, 4, 8),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.78),
            borderRadius: BorderRadius.circular(11),
            border: Border.all(color: color.withValues(alpha: 0.7)),
          ),
          child: Row(
            children: [
              Icon(Icons.auto_awesome, size: 16, color: color),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'AI-проверка · ${componentLabel(check.componentId)}',
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.3,
                      ),
                    ),
                    Text(
                      title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: color,
                        fontSize: 13,
                        fontWeight: FontWeight.w800,
                        height: 1.2,
                      ),
                    ),
                  ],
                ),
              ),
              if (check.latencyMs > 0)
                Text(
                  '${(check.latencyMs / 1000).toStringAsFixed(1)} с',
                  style: const TextStyle(
                    color: AppColors.textMuted,
                    fontSize: 11,
                  ),
                ),
              IconButton(
                tooltip: 'Закрыть результат',
                visualDensity: VisualDensity.compact,
                onPressed: onClose,
                icon: const Icon(Icons.close, size: 17),
                color: AppColors.textMuted,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LiveTelemetryBar extends StatelessWidget {
  const _LiveTelemetryBar({required this.telemetry, this.state});

  final TelemetryMessage? telemetry;
  final LinkState? state;

  bool get _showState {
    final value = state;
    return value != null &&
        value != LinkState.live &&
        value != LinkState.connecting;
  }

  Widget _statusLine() {
    final value = state ?? LinkState.disconnected;
    final color =
        value == LinkState.noResponse || value == LinkState.reconnecting
        ? AppColors.yellow
        : AppColors.red;
    return Row(
      children: [
        Container(
          width: 7,
          height: 7,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 6),
        Text(
          'Телеметрия: ${value.label}',
          style: TextStyle(
            color: color,
            fontSize: 11,
            fontWeight: FontWeight.w700,
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final signals = telemetry?.signals;
    if (signals == null || signals.isEmpty) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.55),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (_showState) ...[_statusLine(), const SizedBox(height: 6)],
            const Row(
              children: [
                Icon(Icons.sensors_off, size: 15, color: AppColors.textMuted),
                SizedBox(width: 8),
                Text(
                  'Телеметрия недоступна — проверьте связь',
                  style: TextStyle(color: AppColors.textMuted, fontSize: 12),
                ),
              ],
            ),
          ],
        ),
      );
    }

    const keys = [
      'vibration_rms',
      'temperature_bearing',
      'pressure_outlet',
      'efficiency',
    ];
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.outline.withValues(alpha: 0.8)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_showState) ...[_statusLine(), const SizedBox(height: 6)],
          Row(
            children: [
              for (var index = 0; index < keys.length; index++) ...[
                if (index > 0)
                  Container(
                    width: 1,
                    height: 26,
                    margin: const EdgeInsets.symmetric(horizontal: 8),
                    color: AppColors.outline,
                  ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        signalLabel(keys[index]).toUpperCase(),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.4,
                        ),
                      ),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Flexible(
                            child: Text(
                              formatSignal(keys[index], signals[keys[index]]),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w800,
                                height: 1.1,
                              ),
                            ),
                          ),
                          const SizedBox(width: 3),
                          Text(
                            signalUnit(keys[index]),
                            style: const TextStyle(
                              color: AppColors.textMuted,
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }
}
