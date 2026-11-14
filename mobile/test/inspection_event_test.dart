import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ntec_inspector/models/frame_result.dart';
import 'package:ntec_inspector/models/inspection_event.dart';
import 'package:ntec_inspector/services/api_service.dart';

void main() {
  test('журнал: новые сверху, лимит 500, старые вытесняются', () {
    final log = InspectionEventLog();
    final start = DateTime(2026, 11, 14, 10, 0, 0);
    for (var index = 0; index < 505; index++) {
      log.add(
        InspectionEvent(
          timestamp: start.add(Duration(seconds: index)),
          frameId: index,
          type: 'info',
          message: 'событие $index',
        ),
      );
    }
    expect(log.length, 500);
    expect(log.events.first.message, 'событие 504');
    expect(log.events.last.message, 'событие 5');
    expect(log.toJson(), hasLength(500));
    log.dispose();
  });

  test('журнал: сериализация события', () {
    final event = InspectionEvent(
      timestamp: DateTime.utc(2026, 11, 14, 7, 8, 9),
      frameId: 123,
      type: 'binding_lost',
      message: 'Маркеры не найдены',
      severity: 'warning',
    );
    final json = event.toJson();
    expect(json['timestamp'], '2026-11-14T07:08:09.000Z');
    expect(json['frame_id'], 123);
    expect(json['type'], 'binding_lost');
    expect(json['message'], 'Маркеры не найдены');
    expect(json['severity'], 'warning');

    final restored = InspectionEvent.fromJson(json);
    expect(restored.frameId, 123);
    expect(restored.type, 'binding_lost');
    expect(restored.severity, 'warning');
    expect(eventTypeLabel('ai_check'), 'AI-проверка');
  });

  test('POST /inspections: события попадают в тело запроса', () async {
    Map<String, dynamic>? body;
    final client = MockClient((request) async {
      body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode(<String, dynamic>{
          'id': 9,
          'equipment_id': 'PUMP-001',
          'created_at': '2026-11-14T10:00:00+00:00',
          'components_status': <dynamic>[],
          'diagnostics': <dynamic>[],
          'analytics': <String, dynamic>{},
        }),
        201,
        headers: const {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final api = ApiService('http://127.0.0.1:8000', client: client);
    addTearDown(api.dispose);

    await api.saveInspection(
      equipmentId: 'PUMP-001',
      detections: const <FrameDetection>[],
      events: [
        InspectionEvent(
          timestamp: DateTime.utc(2026, 11, 14, 7, 8, 9),
          frameId: 42,
          type: 'diagnostic_triggered',
          message: 'Сработала диагностика: Износ подшипника',
          severity: 'warning',
        ),
      ],
    );

    expect(body, isNotNull);
    final events = body!['events'] as List<dynamic>;
    expect(events, hasLength(1));
    final event = events.single as Map<String, dynamic>;
    expect(event['type'], 'diagnostic_triggered');
    expect(event['frame_id'], 42);
    expect(event['message'], contains('Износ подшипника'));
  });
}
