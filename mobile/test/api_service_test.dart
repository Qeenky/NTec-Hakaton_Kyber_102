import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ntec_inspector/services/api_service.dart';

const _analyzeResponse = <String, dynamic>{
  'frame_id': 7,
  'equipment_id': 'PUMP-001',
  'image_width': 1280,
  'image_height': 720,
  'board': {
    'detected': true,
    'markers': 4,
    'confidence': 0.97,
    'reprojection_error': 0.31,
    'reason': null,
  },
  'overlays': [
    {
      'component_id': 'pump',
      'label': 'Насос',
      'polygon': [
        [10, 10],
        [110, 10],
        [110, 90],
        [10, 90],
      ],
      'visible': true,
      'status': 'ok',
      'color': 'green',
    },
    {
      'component_id': 'motor',
      'label': 'Электродвигатель',
      'polygon': [
        [-40, 10],
        [-10, 10],
        [-10, 90],
        [-40, 90],
      ],
      'visible': false,
      'status': 'insufficient_data',
      'color': 'grey',
    },
  ],
  'ocr': {
    'value': 8.4,
    'unit': 'бар',
    'confidence': 0.82,
    'refreshed': true,
    'age_frames': 0,
    'stale': false,
  },
  'detections': [
    {
      'class_id': 1,
      'class_name': 'pump',
      'kind': 'component',
      'confidence': 0.97,
      'bbox': [0.1, 0.1, 0.5, 0.5],
      'source': 'board',
    },
  ],
  'components_status': [
    {'component_id': 'pump', 'status': 'ok'},
    {'component_id': 'motor', 'status': 'insufficient_data'},
  ],
  'active_diagnostics': <dynamic>[],
  'blocked_diagnostics': <dynamic>[],
  'inference_ms': 25,
};

void main() {
  test('POST /analyze: multipart, поле image, парсинг ответа', () async {
    http.Request? captured;
    final client = MockClient((request) async {
      captured = request;
      return http.Response(
        jsonEncode(_analyzeResponse),
        200,
        headers: const {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    final frame = await api.analyzePhoto(
      equipmentId: 'PUMP-001',
      imageBytes: Uint8List.fromList([0xFF, 0xD8, 0xFF, 0xD9]),
    );

    expect(captured, isNotNull);
    expect(captured!.method, 'POST');
    expect(captured!.url.path, '/analyze');
    expect(captured!.url.queryParameters['equipment_id'], 'PUMP-001');
    expect(
      captured!.headers['content-type'],
      contains('multipart/form-data'),
    );
    final body = latin1.decode(captured!.bodyBytes);
    expect(body, contains('name="image"'));
    expect(body, contains('filename="photo.jpg"'));

    expect(frame.frameId, 7);
    expect(frame.hasFrameSize, isTrue);
    expect(frame.overlays, hasLength(2));
    expect(frame.visibleOverlayCount, 1);
    expect(frame.totalOverlayCount, 2);
    expect(frame.isZoneVisible('motor'), isFalse);
    expect(frame.ocr?.displayValue, '8.4');
    expect(frame.inferenceMs, 25);
  });

  test('повторяет запрос при 500 и успешно завершает', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls += 1;
      if (calls < 3) {
        return http.Response('{"detail":"temporary"}', 500);
      }
      return http.Response(
        jsonEncode(_analyzeResponse),
        200,
        headers: const {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    final frame = await api.analyzePhoto(
      equipmentId: 'PUMP-001',
      imageBytes: Uint8List.fromList([1, 2, 3]),
    );
    expect(calls, 3);
    expect(frame.frameId, 7);
  });

  test('не повторяет 404 и отдаёт код ошибки', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls += 1;
      return http.Response('{"detail":"Not Found"}', 404);
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    await expectLater(
      api.analyzePhoto(
        equipmentId: 'PUMP-001',
        imageBytes: Uint8List.fromList([1, 2, 3]),
      ),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 404),
      ),
    );
    expect(calls, 1);
  });

  test('POST /vision/check: multipart, узел в query, парсинг', () async {
    http.Request? captured;
    final client = MockClient((request) async {
      captured = request;
      return http.Response(
        jsonEncode(<String, dynamic>{
          'component_id': 'pump',
          'verdict': 'defect',
          'signs': {
            'corrosion': true,
            'paint_peeling': false,
            'oil_leak': null,
            'mechanical_damage': null,
          },
          'confidence': 0.8,
          'comment': 'Коррозия на корпусе',
          'surface': 'Корпус',
          'model': 'qwen2.5vl:3b',
          'latency_ms': 1500,
          'grounded': true,
          'error': null,
          'cached': false,
        }),
        200,
        headers: const {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    final check = await api.visionCheck(
      equipmentId: 'PUMP-001',
      componentId: 'pump',
      imageBytes: Uint8List.fromList([0xFF, 0xD8, 0xFF, 0xD9]),
    );

    expect(captured, isNotNull);
    expect(captured!.method, 'POST');
    expect(captured!.url.path, '/vision/check');
    expect(captured!.url.queryParameters['equipment_id'], 'PUMP-001');
    expect(captured!.url.queryParameters['component_id'], 'pump');
    expect(captured!.headers['content-type'], contains('multipart/form-data'));
    expect(latin1.decode(captured!.bodyBytes), contains('name="image"'));
    expect(check.verdict, 'defect');
    expect(check.signs.corrosion, isTrue);
    expect(check.signs.oilLeak, isNull);
    expect(check.latencyMs, 1500);
    expect(check.hasError, isFalse);
  });

  test('GET /vision/explain: summary и actions', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/vision/explain');
      expect(request.url.queryParameters['equipment_id'], 'PUMP-001');
      return http.Response(
        jsonEncode(<String, dynamic>{
          'summary': 'Требуется проверка корпуса.',
          'severity': 'warning',
          'actions': ['Осмотреть корпус'],
          'facts_used': ['visual_check'],
          'model': 'qwen2.5vl:3b',
          'latency_ms': 2647,
          'error': null,
        }),
        200,
        headers: const {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    final explain = await api.visionExplain('PUMP-001');
    expect(explain.summary, 'Требуется проверка корпуса.');
    expect(explain.severity, 'warning');
    expect(explain.actions.single, 'Осмотреть корпус');
    expect(explain.factsUsed.single, 'visual_check');
    expect(explain.latencyMs, 2647);
    expect(explain.hasError, isFalse);
  });
}
