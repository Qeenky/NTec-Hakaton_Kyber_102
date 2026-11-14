import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/analytics.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/frame_result.dart';
import '../models/health_info.dart';
import '../models/inspection.dart';
import '../models/inspection_event.dart';
import '../models/telemetry.dart';
import '../models/vision.dart';

/// Ошибка обращения к локальному серверу с готовым русским текстом.
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

/// REST-клиент. Ходит только на адрес из настроек.
class ApiService {
  ApiService(this.baseUrl, {http.Client? client})
      : _client = client ?? http.Client();

  String baseUrl;
  final http.Client _client;

  static const Duration defaultTimeout = Duration(seconds: 6);
  static const Duration healthTimeout = Duration(seconds: 2);
  static const Duration uploadTimeout = Duration(seconds: 15);
  static const Duration visionTimeout = Duration(seconds: 30);

  /// Повторы при сетевых ошибках и 5xx: 300 / 700 / 1500 мс.
  static const List<int> retryDelaysMs = <int>[300, 700, 1500];

  void dispose() => _client.close();

  bool _retryable(ApiException exception) {
    final code = exception.statusCode;
    return code == null || code == 408 || code == 429 || code >= 500;
  }

  /// Первая попытка + до трёх повторов с паузами из [retryDelaysMs].
  Future<T> _retry<T>(Future<T> Function() action) async {
    for (var attempt = 0; ; attempt++) {
      try {
        return await action();
      } on ApiException catch (exception) {
        if (!_retryable(exception) || attempt >= retryDelaysMs.length) {
          rethrow;
        }
        await Future<void>.delayed(
          Duration(milliseconds: retryDelaysMs[attempt]),
        );
      }
    }
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    return query == null ? uri : uri.replace(queryParameters: query);
  }

  Future<http.Response> _get(Uri uri, Duration timeout) async {
    try {
      return await _client.get(uri).timeout(timeout);
    } on TimeoutException {
      throw ApiException('Сервер не ответил за ${timeout.inSeconds} с');
    } on http.ClientException {
      throw ApiException('Нет связи с сервером $baseUrl');
    } on FormatException {
      throw ApiException('Некорректный адрес сервера');
    } catch (_) {
      throw ApiException('Нет связи с сервером $baseUrl');
    }
  }

  Future<http.Response> _getWithRetry(Uri uri, Duration timeout) =>
      _retry(() => _get(uri, timeout));

  Map<String, dynamic> _decodeObject(http.Response response) {
    final decoded = _decode(response);
    if (decoded is Map<String, dynamic>) return decoded;
    if (decoded is Map) return decoded.map((k, v) => MapEntry(k.toString(), v));
    throw ApiException('Неожиданный ответ сервера', statusCode: response.statusCode);
  }

  Object? _decode(http.Response response) {
    final text = utf8.decode(response.bodyBytes, allowMalformed: true);
    try {
      return text.isEmpty ? null : jsonDecode(text);
    } on FormatException {
      throw ApiException('Сервер вернул не JSON', statusCode: response.statusCode);
    }
  }

  Never _throwHttp(http.Response response) {
    String? detail;
    try {
      final decoded = jsonDecode(utf8.decode(response.bodyBytes, allowMalformed: true));
      if (decoded is Map) {
        detail = decoded['detail']?.toString() ??
            decoded['error']?.toString() ??
            decoded['message']?.toString();
      }
    } on FormatException {
      // тело не JSON — покажем код
    }
    if (response.statusCode == 404) {
      throw ApiException(detail ?? 'Не найдено на сервере', statusCode: 404);
    }
    throw ApiException(
      detail ?? 'Ошибка сервера (HTTP ${response.statusCode})',
      statusCode: response.statusCode,
    );
  }

  void _ensureOk(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      _throwHttp(response);
    }
  }

  Future<HealthInfo> fetchHealth({Duration timeout = healthTimeout}) async {
    final response = await _getWithRetry(_uri('/health'), timeout);
    _ensureOk(response);
    return HealthInfo.fromJson(_decodeObject(response));
  }

  Future<List<Equipment>> fetchEquipmentList() async {
    final response =
        await _getWithRetry(_uri('/equipment'), defaultTimeout);
    _ensureOk(response);
    final decoded = _decode(response);
    if (decoded is! List) {
      throw ApiException('Неожиданный формат списка оборудования');
    }
    return decoded
        .whereType<Map>()
        .map((item) => Equipment.fromJson(item.map((k, v) => MapEntry(k.toString(), v))))
        .toList(growable: false);
  }

  Future<Equipment> fetchEquipment(String equipmentId) async {
    final response = await _getWithRetry(
      _uri('/equipment/${Uri.encodeComponent(equipmentId)}'),
      defaultTimeout,
    );
    _ensureOk(response);
    return Equipment.fromJson(_decodeObject(response));
  }

  Future<DiagnosticsSnapshot> fetchDiagnostics(String equipmentId) async {
    final response = await _getWithRetry(
      _uri('/equipment/${Uri.encodeComponent(equipmentId)}/diagnostics'),
      defaultTimeout,
    );
    _ensureOk(response);
    return DiagnosticsSnapshot.fromJson(_decodeObject(response));
  }

  Future<TelemetryHistory> fetchTelemetry(
    String equipmentId, {
    int minutes = 5,
    int limit = 3000,
  }) async {
    final response = await _getWithRetry(
      _uri(
        '/equipment/${Uri.encodeComponent(equipmentId)}/telemetry',
        <String, String>{'minutes': '$minutes', 'limit': '$limit'},
      ),
      defaultTimeout,
    );
    _ensureOk(response);
    return TelemetryHistory.fromJson(_decodeObject(response));
  }

  Future<FrameAnalytics> fetchAnalytics(String equipmentId) async {
    final response = await _getWithRetry(
      _uri('/equipment/${Uri.encodeComponent(equipmentId)}/analytics'),
      defaultTimeout,
    );
    _ensureOk(response);
    return FrameAnalytics.fromJson(_decodeObject(response));
  }

  Future<List<InspectionRecord>> fetchInspections(String equipmentId) async {
    final response = await _getWithRetry(
      _uri('/inspections', <String, String>{'equipment_id': equipmentId}),
      defaultTimeout,
    );
    _ensureOk(response);
    final decoded = _decode(response);
    if (decoded is! List) {
      throw ApiException('Неожиданный формат истории осмотров');
    }
    return decoded
        .whereType<Map>()
        .map((item) =>
            InspectionRecord.fromJson(item.map((k, v) => MapEntry(k.toString(), v))))
        .toList(growable: false);
  }

  /// Сохраняет осмотр. Тело — по контракту `InspectionCreate`.
  Future<SavedInspection> saveInspection({
    required String equipmentId,
    required List<FrameDetection> detections,
    int? frameId,
    int? imageWidth,
    int? imageHeight,
    int? inferenceMs,
    List<InspectionEvent> events = const <InspectionEvent>[],
  }) async {
    final body = <String, dynamic>{
      'equipment_id': equipmentId,
      'detections': detections
          .where((detection) => detection.bbox.length == 4)
          .map((detection) => detection.toInspectionJson())
          .toList(growable: false),
      if (frameId != null && frameId > 0) 'frame_id': frameId,
      if (imageWidth != null && imageWidth > 0) 'image_width': imageWidth,
      if (imageHeight != null && imageHeight > 0) 'image_height': imageHeight,
      if (inferenceMs != null && inferenceMs >= 0) 'inference_ms': inferenceMs,
      if (events.isNotEmpty)
        'events': events.map((event) => event.toJson()).toList(growable: false),
    };

    final http.Response response = await _retry(() async {
      final http.Response result;
      try {
        result = await _client
            .post(
              _uri('/inspections'),
              headers: const <String, String>{
                'Content-Type': 'application/json; charset=utf-8',
              },
              body: jsonEncode(body),
            )
            .timeout(defaultTimeout);
      } on TimeoutException {
        throw ApiException('Сервер не ответил за ${defaultTimeout.inSeconds} с');
      } on http.ClientException {
        throw ApiException('Нет связи с сервером $baseUrl');
      } catch (_) {
        throw ApiException('Нет связи с сервером $baseUrl');
      }
      _ensureOk(result);
      return result;
    });
    return SavedInspection.fromJson(_decodeObject(response));
  }

  /// Загрузка фото на `POST /analyze?equipment_id=...` (multipart, поле `image`).
  /// Ответ — та же структура, что у `/ws/frame`.
  Future<FrameResult> analyzePhoto({
    required String equipmentId,
    required Uint8List imageBytes,
    String filename = 'photo.jpg',
  }) async {
    final response = await _retry(() async {
      final http.Response result;
      try {
        final request = http.MultipartRequest(
          'POST',
          _uri(
            '/analyze',
            <String, String>{'equipment_id': equipmentId},
          ),
        );
        request.files.add(
          http.MultipartFile.fromBytes(
            'image',
            imageBytes,
            filename: filename,
          ),
        );
        final streamed =
            await _client.send(request).timeout(uploadTimeout);
        result = await http.Response.fromStream(streamed);
      } on TimeoutException {
        throw ApiException(
          'Сервер не ответил за ${uploadTimeout.inSeconds} с',
        );
      } on ApiException {
        rethrow;
      } on http.ClientException {
        throw ApiException('Нет связи с сервером $baseUrl');
      } catch (_) {
        throw ApiException('Нет связи с сервером $baseUrl');
      }
      _ensureOk(result);
      return result;
    });
    return FrameResult.fromJson(_decodeObject(response));
  }

  /// Проверка узла локальной VLM: `POST /vision/check` (multipart, поле `image`).
  Future<VisionCheck> visionCheck({
    required String equipmentId,
    required String componentId,
    required Uint8List imageBytes,
    String filename = 'component.jpg',
  }) async {
    final started = DateTime.now();
    final response = await _retry(() async {
      final elapsed = DateTime.now().difference(started);
      final remaining = visionTimeout - elapsed;
      if (remaining <= Duration.zero) {
        throw ApiException(
          'Модель не ответила за ${visionTimeout.inSeconds} с',
        );
      }
      final http.Response result;
      try {
        final request = http.MultipartRequest(
          'POST',
          _uri(
            '/vision/check',
            <String, String>{
              'equipment_id': equipmentId,
              'component_id': componentId,
            },
          ),
        );
        request.files.add(
          http.MultipartFile.fromBytes(
            'image',
            imageBytes,
            filename: filename,
          ),
        );
        final streamed = await _client.send(request).timeout(remaining);
        result = await http.Response.fromStream(streamed);
      } on TimeoutException {
        throw ApiException(
          'Модель не ответила за ${visionTimeout.inSeconds} с',
        );
      } on ApiException {
        rethrow;
      } on http.ClientException {
        throw ApiException('Нет связи с сервером $baseUrl');
      } catch (_) {
        throw ApiException('Нет связи с сервером $baseUrl');
      }
      _ensureOk(result);
      return result;
    });
    return VisionCheck.fromJson(_decodeObject(response));
  }

  /// Итоговое заключение локальной модели: `GET /vision/explain`.
  Future<VisionExplain> visionExplain(String equipmentId) async {
    final response = await _getWithRetry(
      _uri('/vision/explain', <String, String>{'equipment_id': equipmentId}),
      visionTimeout,
    );
    _ensureOk(response);
    return VisionExplain.fromJson(_decodeObject(response));
  }
}
