import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/diagnostic.dart';
import 'package:ntec_inspector/models/equipment.dart';
import 'package:ntec_inspector/models/frame_result.dart';
import 'package:ntec_inspector/models/inspection.dart';
import 'package:ntec_inspector/models/telemetry.dart';

void main() {
  group('Equipment', () {
    final json = <String, dynamic>{
      'id': 'PUMP-001',
      'name': 'Насосный агрегат',
      'model': 'DEMO-PUMP',
      'serial': 'DEMO-001',
      'year': 2021,
      'runtime_hours': 12450.5,
      'normative_life_years': 10,
      'last_service_date': '2026-04-18',
      'qr_code': 'PUMP-001',
      'location': 'Насосная станция №1',
      'components_count': 5,
      'baseline': {'vibration_rms': 2.8},
      'components': [
        {
          'id': 1,
          'name': 'motor',
          'kind': 'component',
          'condition': 'ok',
          'normative_life_years': 8,
          'installed_date': '2021-06-15',
          'remaining_resource': {
            'value': 2.5,
            'status': 'ok',
            'is_estimate': true,
            'basis': {'formula': 'test'},
          },
        },
        {
          'id': 2,
          'name': 'manometer',
          'kind': 'component',
          'condition': 'possible_defect',
          'normative_life_years': 5,
          'installed_date': '2023-03-10',
          'remaining_resource': {
            'value': 1.2,
            'status': 'ok',
            'is_estimate': true,
            'basis': <String, dynamic>{},
          },
        },
      ],
    };

    test('парсит карточку и остаточный ресурс', () {
      final equipment = Equipment.fromJson(json);
      expect(equipment.id, 'PUMP-001');
      expect(equipment.runtimeHours, 12450.5);
      expect(equipment.components, hasLength(2));
      expect(equipment.components.first.name, 'motor');
      expect(equipment.components.first.remainingResource?.text,
          '≈ 2.5 лет · оценка');
      expect(equipment.countByCondition('ok'), 1);
      expect(equipment.componentsCount, 5);
    });

    test('худшее состояние выбирается по приоритету', () {
      expect(Equipment.fromJson(json).worstCondition, 'possible_defect');
      final healthy = Equipment.fromJson({
        ...json,
        'components': <dynamic>[],
      });
      expect(healthy.worstCondition, 'ok');
    });
  });

  group('FrameResult', () {
    final frameJson = <String, dynamic>{
      'frame_id': 12,
      'equipment_id': 'PUMP-001',
      'image_width': 1280,
      'image_height': 720,
      'board': {
        'detected': true,
        'markers': 4,
        'marker_ids': [0, 1, 2, 3],
        'confidence': 0.977,
        'reprojection_error': 0.3,
        'reason': null,
      },
      'overlays': [
        {
          'component_id': 'manometer',
          'label': 'Манометр',
          'polygon': [
            [838.2, 239.1],
            [912.6, 239.1],
            [912.6, 331.5],
            [838.2, 331.5],
          ],
          'visible': true,
          'status': 'possible_defect',
          'color': 'red',
          'confidence': 0.91,
          'defects': ['fog'],
        },
      ],
      'ocr': {
        'value': 8.383,
        'unit': 'бар',
        'confidence': 0.837,
        'error': null,
        'refreshed': false,
        'age_frames': 3,
        'stale': false,
      },
      'detections': [
        {
          'class_id': 4,
          'class_name': 'manometer',
          'kind': 'component',
          'confidence': 0.977,
          'bbox': [0.654, 0.332, 0.713, 0.46],
          'source': 'board',
        },
        {
          'class_id': 5,
          'class_name': 'oil_leak',
          'kind': 'defect',
          'confidence': 0.7,
          'bbox': [0.1, 0.2, 0.3, 0.4],
          'source': 'board',
        },
      ],
      'components_status': [
        {'component_id': 'manometer', 'status': 'possible_defect', 'defects': []},
      ],
      'active_diagnostics': [
        {
          'id': 41,
          'rule_id': 'gauge_drift',
          'title': 'Расхождение показаний манометра',
          'severity': 'warning',
          'status': 'active',
          'confidence': 1.0,
          'message': 'Показания расходятся',
          'recommendation': 'Провести поверку',
          'evidence': [
            {
              'op': 'deviation_pct',
              'actual': 22.64,
              'baseline': 8.467,
              'threshold': 15.0,
              'matched': true,
              'weight': 1.0,
              'score': 1.0,
            },
          ],
          'overlay': {
            'component_id': 'manometer',
            'marker': 'badge',
            'color': 'orange',
            'label': 'Поверка манометра',
          },
        },
      ],
      'blocked_diagnostics': [
        {
          'rule_id': 'guard_removed',
          'title': 'Кожух муфты отсутствует',
          'severity': 'critical',
          'blocked_by': 'camera_unavailable',
          'reason': 'нет свежих кадров с камеры',
        },
      ],
      'inference_ms': 24,
    };

    test('парсит оверлеи, OCR и диагностику', () {
      final frame = FrameResult.fromJson(frameJson);
      expect(frame.frameId, 12);
      expect(frame.hasFrameSize, isTrue);
      expect(frame.overlays, hasLength(1));
      expect(frame.overlays.first.polygon, hasLength(4));
      expect(frame.overlays.first.color, 'red');
      expect(frame.overlays.first.hasGeometry, isTrue);
      expect(frame.ocr?.displayValue, '8.4');
      expect(frame.ocr?.unit, 'бар');
      expect(frame.ocr?.ageFrames, 3);
      expect(frame.activeDiagnostics.single.title,
          'Расхождение показаний манометра');
      expect(frame.activeDiagnostics.single.evidence.single.matched, isTrue);
      expect(
        frame.activeDiagnostics.single.evidence.single.description,
        contains('отклонение'),
      );
      expect(frame.blockedDiagnostics.single.blockedBy, 'camera_unavailable');
      expect(frame.statusFor('manometer'), 'possible_defect');
      expect(frame.visibleOverlayCount, 1);
      expect(frame.totalOverlayCount, 1);
      expect(frame.isZoneVisible('manometer'), isTrue);
      expect(frame.isZoneVisible('motor'), isTrue);
      expect(frame.defects.single.className, 'oil_leak');
      expect(frame.inferenceMs, 24);
    });

    test('детекции сериализуются в тело /inspections', () {
      final detection = FrameDetection.fromJson(
        (frameJson['detections'] as List).first as Map<String, dynamic>,
      );
      final body = detection.toInspectionJson();
      expect(body.keys, containsAll(<String>[
        'class_id',
        'class_name',
        'kind',
        'confidence',
        'bbox',
      ]));
      expect(body['bbox'], hasLength(4));
      expect(body['class_id'], 4);
    });

    test('битый/пустой ответ не роняет парсер', () {
      final frame = FrameResult.fromJson(<String, dynamic>{
        'error': 'invalid_image',
        'board': {'detected': false, 'reason': 'invalid_image'},
        'overlays': null,
        'ocr': null,
      });
      expect(frame.error, 'invalid_image');
      expect(frame.board.detected, isFalse);
      expect(frame.hasFrameSize, isFalse);
      expect(frame.overlays, isEmpty);
      expect(FrameResult.empty.imageWidth, 0);
      expect(FrameResult.empty.visibleOverlayCount, 0);
      expect(FrameResult.empty.totalOverlayCount, 5);
    });
  });

  test('телеметрия: серии и последнее значение', () {
    final history = TelemetryHistory.fromJson(<String, dynamic>{
      'equipment_id': 'PUMP-001',
      'minutes': 5,
      'samples': [
        {
          'ts': '2026-11-13T07:48:00+00:00',
          'scenario': 'normal',
          'signals': {'vibration_rms': 2.9, 'temperature_bearing': 48.1},
        },
        {
          'ts': '2026-11-13T07:48:01+00:00',
          'scenario': 'normal',
          'signals': {'vibration_rms': 3.1, 'temperature_bearing': 48.4},
        },
      ],
      'latest': {
        'ts': '2026-11-13T07:48:01+00:00',
        'scenario': 'normal',
        'signals': {'vibration_rms': 3.1, 'temperature_bearing': 48.4},
      },
    });
    expect(history.samples, hasLength(2));
    expect(history.series('vibration_rms'), <double>[2.9, 3.1]);
    expect(history.latestSignal('temperature_bearing'), 48.4);
    expect(history.minMaxDelta('vibration_rms'), closeTo(0.2, 1e-9));
    expect(history.series('pressure_outlet'), isEmpty);
  });

  test('история осмотров: summary и статусы', () {
    final record = InspectionRecord.fromJson(<String, dynamic>{
      'id': 7,
      'equipment_id': 'PUMP-001',
      'created_at': '2026-11-13T10:00:00+00:00',
      'summary': {
        'frame_id': 12,
        'inference_ms': 24,
        'detections': [
          {
            'class_id': 5,
            'class_name': 'oil_leak',
            'kind': 'defect',
            'confidence': 0.7,
            'bbox': [0.1, 0.2, 0.3, 0.4],
          },
        ],
        'components_status': [
          {'component_id': 'manometer', 'status': 'check_required'},
        ],
        'diagnostics': [
          {
            'rule_id': 'gauge_drift',
            'title': 'Дрейф',
            'severity': 'warning',
            'status': 'active',
          },
        ],
      },
      'diagnostics': <dynamic>[],
    });
    expect(record.id, 7);
    expect(record.summary.defectCount, 1);
    expect(record.summary.problemZones, 1);
    expect(record.summary.shortStatus, 'дефектов: 1');
    expect(record.summary.diagnostics.single.title, 'Дрейф');
    expect(record.parsedAt, isNotNull);
  });

  test('blocked-диагностика объясняет причину', () {
    final blocked = BlockedDiagnostic.fromJson(<String, dynamic>{
      'rule_id': 'guard_removed',
      'severity': 'critical',
      'blocked_by': 'signal_missing:vibration_hf',
    });
    expect(blocked.title, 'guard_removed');
    expect(blocked.blockedBy, 'signal_missing:vibration_hf');
  });
}
