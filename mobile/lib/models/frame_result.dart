import 'analytics.dart';
import 'diagnostic.dart';
import 'json_utils.dart';
import 'overlay_geometry.dart';
import 'vision.dart';
import 'verdict.dart';

/// Состояние AR-планшета в кадре.
class BoardInfo {
  const BoardInfo({
    required this.detected,
    this.markers = 0,
    this.markerIds = const <int>[],
    this.confidence = 0,
    this.stale = false,
    this.reprojectionError,
    this.coverage,
    this.valid,
    this.method,
    this.reason,
    this.corners = const <List<double>>[],
    this.frameSize = const <int>[],
  });

  final bool detected;
  final int markers;
  final List<int> markerIds;
  final double confidence;
  final bool stale;
  final double? reprojectionError;
  final double? coverage;
  final bool? valid;
  final String? method;
  final String? reason;
  final List<List<double>> corners;
  final List<int> frameSize;

  factory BoardInfo.fromJson(Map<String, dynamic> json) {
    return BoardInfo(
      detected: asBool(json['detected']) ?? false,
      markers: asInt(json['markers']) ?? 0,
      markerIds: (json['marker_ids'] is List)
          ? (json['marker_ids'] as List)
                .map((item) => asInt(item))
                .whereType<int>()
                .toList(growable: false)
          : const <int>[],
      confidence: asDouble(json['confidence']) ?? 0,
      stale: asBool(json['stale']) ?? false,
      reprojectionError: asDouble(json['reprojection_error']),
      coverage: asDouble(json['coverage']),
      valid: asBool(json['valid']),
      method: asString(json['method']),
      reason: asString(json['reason']),
      corners: asPolygon(json['corners']),
      frameSize: (json['frame_size'] is List)
          ? (json['frame_size'] as List)
                .map((item) => asInt(item))
                .whereType<int>()
                .toList(growable: false)
          : const <int>[],
    );
  }

  static const unknown = BoardInfo(detected: false);
}

/// Одна зона AR-планшета из `overlays`.
class OverlayZone {
  const OverlayZone({
    required this.componentId,
    required this.label,
    required this.polygon,
    required this.visible,
    required this.status,
    required this.color,
    this.confidence,
    this.defects = const <String>[],
    this.areaPx,
  });

  final String componentId;
  final String label;
  final List<List<double>> polygon;
  final bool visible;
  final String status;
  final String color;
  final double? confidence;
  final List<String> defects;
  final double? areaPx;

  factory OverlayZone.fromJson(Map<String, dynamic> json) {
    final rawDefects = json['defects'];
    return OverlayZone(
      componentId: asString(json['component_id']) ?? '',
      label: asString(json['label']) ?? '',
      polygon: asPolygon(json['polygon']),
      visible: asBool(json['visible']) ?? true,
      status: asString(json['status']) ?? 'insufficient_data',
      color: asString(json['color']) ?? 'grey',
      confidence: asDouble(json['confidence']),
      defects: rawDefects is List
          ? rawDefects.map((item) => item.toString()).toList(growable: false)
          : const <String>[],
      areaPx: asDouble(json['area_px']),
    );
  }

  bool get hasGeometry => polygon.length >= 3;
}

/// Показание OCR манометра.
class OcrReading {
  const OcrReading({
    required this.value,
    required this.unit,
    required this.confidence,
    required this.refreshed,
    required this.ageFrames,
    required this.stale,
    this.rawValue,
    this.error,
    this.method,
    this.ageSec,
    this.angleDeg,
  });

  final double? value;
  final double? rawValue;
  final String unit;
  final double confidence;
  final String? error;
  final String? method;
  final bool refreshed;
  final int ageFrames;
  final bool stale;
  final double? ageSec;
  final double? angleDeg;

  factory OcrReading.fromJson(Map<String, dynamic> json) {
    return OcrReading(
      value: asDouble(json['value']),
      rawValue: asDouble(json['raw_value']),
      unit: asString(json['unit']) ?? '',
      confidence: asDouble(json['confidence']) ?? 0,
      error: asString(json['error']),
      method: asString(json['method']),
      refreshed: asBool(json['refreshed']) ?? false,
      ageFrames: asInt(json['age_frames']) ?? 0,
      stale: asBool(json['stale']) ?? false,
      ageSec: asDouble(json['age_sec']),
      angleDeg: asDouble(json['angle_deg']),
    );
  }

  String get displayValue {
    final number = value;
    if (number == null) return '—';
    return number.toStringAsFixed(1);
  }
}

/// Детекция для сохранения в `/inspections`.
class FrameDetection {
  const FrameDetection({
    required this.classId,
    required this.className,
    required this.kind,
    required this.confidence,
    required this.bbox,
    this.source,
  });

  final int classId;
  final String className;
  final String kind;
  final double confidence;
  final List<double> bbox;
  final String? source;

  factory FrameDetection.fromJson(Map<String, dynamic> json) {
    return FrameDetection(
      classId: asInt(json['class_id']) ?? 0,
      className: asString(json['class_name']) ?? '',
      kind: asString(json['kind']) ?? 'component',
      confidence: asDouble(json['confidence']) ?? 0,
      bbox: asBbox(json['bbox']),
      source: asString(json['source']),
    );
  }

  bool get isDefect => kind == 'defect';

  Map<String, dynamic> toInspectionJson() => <String, dynamic>{
    'class_id': classId,
    'class_name': className,
    'kind': kind,
    'confidence': confidence,
    'bbox': bbox,
  };
}

/// Статус узла из БД-состава (`components_status`).
class FrameComponentStatus {
  const FrameComponentStatus({
    required this.componentId,
    required this.status,
    this.confidence,
    this.defects = const <String>[],
  });

  final String componentId;
  final String status;
  final double? confidence;
  final List<String> defects;

  factory FrameComponentStatus.fromJson(Map<String, dynamic> json) {
    final rawDefects = json['defects'];
    return FrameComponentStatus(
      componentId: asString(json['component_id']) ?? '',
      status: asString(json['status']) ?? 'insufficient_data',
      confidence: asDouble(json['confidence']),
      defects: rawDefects is List
          ? rawDefects.map((item) => item.toString()).toList(growable: false)
          : const <String>[],
    );
  }
}

/// Полный ответ `/ws/frame` на один отправленный JPEG.
class FrameResult {
  const FrameResult({
    required this.frameId,
    required this.equipmentId,
    required this.imageWidth,
    required this.imageHeight,
    required this.board,
    required this.overlays,
    required this.ocr,
    required this.detections,
    required this.componentsStatus,
    required this.activeDiagnostics,
    required this.blockedDiagnostics,
    this.normalDiagnostics = const <NormalDiagnostic>[],
    required this.inferenceMs,
    this.vision,
    this.analytics,
    this.error,
    this.receivedAt,
  });

  final int frameId;
  final String equipmentId;
  final int imageWidth;
  final int imageHeight;
  final BoardInfo board;
  final List<OverlayZone> overlays;
  final OcrReading? ocr;
  final List<FrameDetection> detections;
  final List<FrameComponentStatus> componentsStatus;
  final List<DiagnosticEvent> activeDiagnostics;
  final List<BlockedDiagnostic> blockedDiagnostics;
  final List<NormalDiagnostic> normalDiagnostics;
  final int inferenceMs;
  final VisionCheck? vision;
  final FrameAnalytics? analytics;
  final String? error;
  final DateTime? receivedAt;

  factory FrameResult.fromJson(Map<String, dynamic> json) {
    final rawBoard = json['board'];
    final rawOcr = json['ocr'];
    final rawVision = json['vision'];
    return FrameResult(
      frameId: asInt(json['frame_id']) ?? 0,
      equipmentId: asString(json['equipment_id']) ?? '',
      imageWidth: asInt(json['image_width']) ?? 0,
      imageHeight: asInt(json['image_height']) ?? 0,
      board: rawBoard is Map
          ? BoardInfo.fromJson(asMap(rawBoard))
          : BoardInfo.unknown,
      overlays: asMapList(json['overlays'])
          .map(OverlayZone.fromJson)
          .toList(growable: false),
      ocr: rawOcr is Map ? OcrReading.fromJson(asMap(rawOcr)) : null,
      detections: asMapList(json['detections'])
          .map(FrameDetection.fromJson)
          .toList(growable: false),
      componentsStatus: asMapList(json['components_status'])
          .map(FrameComponentStatus.fromJson)
          .toList(growable: false),
      activeDiagnostics: asMapList(json['active_diagnostics'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      blockedDiagnostics: asMapList(json['blocked_diagnostics'])
          .map(BlockedDiagnostic.fromJson)
          .toList(growable: false),
      normalDiagnostics: asMapList(json['normal_diagnostics'])
          .map(NormalDiagnostic.fromJson)
          .toList(growable: false),
      inferenceMs: asInt(json['inference_ms']) ?? 0,
      vision: rawVision is Map ? VisionCheck.fromJson(asMap(rawVision)) : null,
      analytics: FrameAnalytics.fromJsonOrNull(json['analytics']),
      error: asString(json['error']),
      receivedAt: DateTime.now(),
    );
  }

  bool get hasFrameSize => imageWidth > 0 && imageHeight > 0;

  /// Итог осмотра по статусам узлов, оверлеям и окну аналитики.
  InspectionVerdict resolveVerdict(Iterable<String> roster) {
    final observations = <VerdictObservation>[];
    for (final zone in overlays) {
      observations.add(
        VerdictObservation(
          componentId: zone.componentId,
          status: zone.status,
          seen: zone.visible && zone.hasGeometry,
        ),
      );
    }
    for (final item in componentsStatus) {
      observations.add(
        VerdictObservation(
          componentId: item.componentId,
          status: item.status,
          seen: item.status != 'insufficient_data',
        ),
      );
    }
    final window = analytics;
    if (window != null) {
      for (final component in window.components) {
        observations.add(
          VerdictObservation(
            componentId: component.componentId,
            status: component.confirmedDefects.isNotEmpty
                ? 'possible_defect'
                : component.statusWindow,
            seen: !component.outOfFrame,
          ),
        );
      }
    }
    return InspectionVerdict.resolve(
      roster: roster,
      observations: observations,
      hasDefectEvidence: detections.any((item) => item.isDefect),
    );
  }

  /// Сколько зон реально видно в кадре (для «Видно N из 5»).
  int get visibleOverlayCount =>
      overlays.where((zone) => zone.visible && zone.hasGeometry).length;

  /// Всего зон: из ответа, из состава БД или стандартные пять.
  int get totalOverlayCount {
    if (overlays.isNotEmpty) return overlays.length;
    if (componentsStatus.isNotEmpty) return componentsStatus.length;
    return 5;
  }

  bool isZoneVisible(String componentId) {
    for (final zone in overlays) {
      if (zone.componentId == componentId) {
        return zone.visible && zone.hasGeometry;
      }
    }
    return true;
  }

  /// Прозрачность оверлеев: при устаревшей позе — 30%.
  double get overlayOpacity => board.stale ? 0.3 : 1.0;

  /// Оверлеи, которые можно рисовать. При потере привязки — пусто.
  List<OverlayZone> drawableOverlays({bool hideOverlays = false}) {
    if (hideOverlays || !board.detected || !hasFrameSize) {
      return const <OverlayZone>[];
    }
    return overlays
        .where((zone) {
          if (!zone.visible || !zone.hasGeometry) return false;
          final ratio = outsideAreaRatio(
            zone.polygon,
            imageWidth.toDouble(),
            imageHeight.toDouble(),
          );
          return ratio <= 0.2;
        })
        .toList(growable: false);
  }

  List<FrameDetection> get defects => detections
      .where((detection) => detection.isDefect)
      .toList(growable: false);

  /// Итоговый статус узла: из `overlays` (AR) или `components_status` (БД).
  String statusFor(String componentId) {
    for (final zone in overlays) {
      if (zone.componentId == componentId) return zone.status;
    }
    for (final item in componentsStatus) {
      if (item.componentId == componentId) return item.status;
    }
    return 'insufficient_data';
  }

  static const empty = FrameResult(
    frameId: 0,
    equipmentId: '',
    imageWidth: 0,
    imageHeight: 0,
    board: BoardInfo.unknown,
    overlays: <OverlayZone>[],
    ocr: null,
    detections: <FrameDetection>[],
    componentsStatus: <FrameComponentStatus>[],
    activeDiagnostics: <DiagnosticEvent>[],
    blockedDiagnostics: <BlockedDiagnostic>[],
    inferenceMs: 0,
  );
}
