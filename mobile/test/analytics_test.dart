import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/analytics.dart';
import 'package:ntec_inspector/models/frame_result.dart';
import 'package:ntec_inspector/models/inspection.dart';

void main() {
  test('analytics: новый формат с окном и подтверждёнными дефектами', () {
    final analytics = FrameAnalytics.fromJsonOrNull(<String, dynamic>{
      'window_sec': 60,
      'frames': 42,
      'duration_sec': 18.5,
      'components': [
        {
          'component_id': 'pump',
          'label': 'Насос',
          'visibility_ratio': 0.86,
          'frames_seen': 36,
          'frames_total': 42,
          'last_seen_ago_sec': 0.4,
          'status_window': 'possible_defect',
          'confidence': 0.61,
          'defects': [
            {
              'class_name': 'corrosion',
              'presence_ratio': 0.52,
              'mean_confidence': 0.61,
              'confirmed': true,
            },
            {
              'class_name': 'oil_leak',
              'presence_ratio': 0.1,
              'mean_confidence': 0.3,
              'confirmed': false,
            },
          ],
          'note': 'виден 36 из 42 кадров',
        },
        {
          'component_id': 'motor',
          'label': 'Электродвигатель',
          'visibility_ratio': 0,
          'frames_seen': 0,
          'frames_total': 42,
          'status_window': 'insufficient_data',
          'confidence': 0,
          'defects': <dynamic>[],
        },
      ],
    });

    expect(analytics, isNotNull);
    expect(analytics!.frames, 42);
    expect(analytics.durationSec, 18.5);
    expect(analytics.headerText, 'Наблюдение · 18.5 с · 42 кадров');
    expect(analytics.components, hasLength(2));

    final pump = analytics.components.first;
    expect(pump.componentId, 'pump');
    expect(pump.label, 'Насос');
    expect(pump.visibilityRatio, closeTo(0.86, 1e-9));
    expect(pump.framesSeen, 36);
    expect(pump.framesTotal, 42);
    expect(pump.statusWindow, 'possible_defect');
    expect(pump.confirmedDefects, hasLength(1));
    expect(pump.confirmedDefects.single.label, 'коррозия');
    expect(pump.outOfFrame, isFalse);
    expect(pump.sortPriority, 0);

    final motor = analytics.components.last;
    expect(motor.outOfFrame, isTrue);
    expect(motor.sortPriority, 3);
  });

  test('analytics: старый компактный формат /ws/frame', () {
    final analytics = FrameAnalytics.fromJsonOrNull(<String, dynamic>{
      'window_sec': 60,
      'frames': 10,
      'duration_sec': 4.0,
      'components': [
        {
          'component_id': 'flange',
          'visible_ratio': 0.5,
          'frames_seen': 5,
          'confirmed_defects': [
            {
              'class_name': 'corrosion',
              'presence_ratio': 0.6,
              'mean_confidence': 0.7,
            },
          ],
        },
      ],
    });
    expect(analytics, isNotNull);
    final flange = analytics!.components.single;
    expect(flange.visibilityRatio, 0.5);
    expect(flange.framesTotal, 10);
    expect(flange.defects.single.confirmed, isTrue);
    expect(flange.statusWindow, 'possible_defect');
  });

  test('analytics: поле отсутствует или пустое → null', () {
    expect(FrameAnalytics.fromJsonOrNull(null), isNull);
    expect(FrameAnalytics.fromJsonOrNull(<String, dynamic>{}), isNull);
    expect(
      FrameAnalytics.fromJsonOrNull(<String, dynamic>{
        'frames': 0,
        'components': <dynamic>[],
      }),
      isNull,
    );

    final frame = FrameResult.fromJson(<String, dynamic>{
      'board': {'detected': false},
    });
    expect(frame.analytics, isNull);
  });

  test('analytics: signs со всеми статусами и fallback названий', () {
    final component = AnalyticsComponent.fromJson(<String, dynamic>{
      'component_id': 'pump',
      'label': 'Насос',
      'visibility_ratio': 0.8,
      'frames_seen': 40,
      'frames_total': 50,
      'status_window': 'possible_defect',
      'signs': [
        {
          'sign': 'corrosion',
          'label': 'Коррозия',
          'status': 'detected',
          'sources': ['heuristics', 'vlm'],
          'confidence': 0.61,
          'presence_ratio': 0.52,
          'frames_present': 18,
          'first_seen_ago_sec': 22.4,
          'last_seen_ago_sec': 0.3,
          'trend': 'growing',
        },
        {
          'sign': 'oil_leak',
          'status': 'single_observation',
          'confidence': 0.4,
          'presence_ratio': 0.1,
          'frames_present': 2,
        },
        {
          'sign': 'paint_peeling',
          'status': 'not_detected',
        },
        {
          'sign': 'crack',
          'status': 'not_visible',
        },
      ],
    });

    expect(component.signs, hasLength(4));
    final corrosion = component.signs.first;
    expect(corrosion.sign, 'corrosion');
    expect(corrosion.label, 'Коррозия');
    expect(corrosion.status, 'detected');
    expect(corrosion.isDetected, isTrue);
    expect(corrosion.isShown, isTrue);
    expect(corrosion.sources, <String>['heuristics', 'vlm']);
    expect(corrosion.confidence, 0.61);
    expect(corrosion.framesPresent, 18);
    expect(corrosion.trend, 'growing');
    expect(component.signs[1].isSingle, isTrue);
    expect(component.signs[1].label, 'Подтёк масла');
    expect(component.signs[2].isNotDetected, isTrue);
    expect(component.signs[3].isNotVisible, isTrue);
  });

  test('analytics: signs отсутствует — пустой список, названия по словарю', () {
    final component = AnalyticsComponent.fromJson(<String, dynamic>{
      'component_id': 'motor',
      'frames_seen': 5,
      'frames_total': 10,
      'visibility_ratio': 0.5,
    });
    expect(component.signs, isEmpty);
    expect(component.defects, isEmpty);

    expect(visualSignDefaultLabel('oxidation'), 'Окисление контактов');
    expect(visualSignDefaultLabel('guard_missing'), 'Отсутствие кожуха');
    expect(visualSignDefaultLabel('paint_peeling'), 'Отслоение краски');
    expect(
      VisualSign.fromJson(<String, dynamic>{
        'sign': 'oxidation',
        'status': 'detected',
      }).label,
      'Окисление контактов',
    );
    expect(
      VisualSign.fromJson(<String, dynamic>{
        'sign': 'crack',
        'label': 'Своя подпись',
        'status': 'detected',
      }).label,
      'Своя подпись',
    );
  });

  test('analytics: signs приходят в FrameResult и влияют на signature', () {
    final frame = FrameResult.fromJson(<String, dynamic>{
      'board': {'detected': true},
      'analytics': {
        'frames': 10,
        'duration_sec': 5.0,
        'components': [
          {
            'component_id': 'pump',
            'visibility_ratio': 1.0,
            'frames_seen': 10,
            'status_window': 'possible_defect',
            'signs': [
              {
                'sign': 'corrosion',
                'status': 'detected',
                'confidence': 0.7,
                'frames_present': 6,
              },
            ],
          },
        ],
      },
    });
    final sign = frame.analytics!.components.single.signs.single;
    expect(sign.isDetected, isTrue);
    expect(frame.analytics!.signature(), contains('corrosion:detected'));
  });

  test('analytics: сортировка и добавление отсутствовавших узлов', () {
    final analytics = FrameAnalytics.fromJson(<String, dynamic>{
      'frames': 20,
      'duration_sec': 10.0,
      'components': [
        {
          'component_id': 'pump',
          'visibility_ratio': 0.9,
          'frames_seen': 18,
          'status_window': 'ok',
        },
        {
          'component_id': 'manometer',
          'visibility_ratio': 0.4,
          'frames_seen': 8,
          'status_window': 'check_required',
        },
      ],
    });
    final rows = analytics.withRoster(<String>[
      'pump',
      'manometer',
      'motor',
      'flange',
    ]);
    expect(rows.map((item) => item.componentId).toList(), <String>[
      'manometer',
      'pump',
      'flange',
      'motor',
    ]);
    expect(rows.where((item) => item.outOfFrame), hasLength(2));
  });

  test('analytics из сохранённого осмотра (/inspections, верхний уровень)', () {
    final record = InspectionRecord.fromJson(<String, dynamic>{
      'id': 8,
      'equipment_id': 'PUMP-001',
      'created_at': '2026-11-13T10:00:00+00:00',
      'summary': {
        'frame_id': 12,
        'detections': <dynamic>[],
        'components_status': <dynamic>[],
        'diagnostics': <dynamic>[],
      },
      'diagnostics': <dynamic>[],
      'analytics': {
        'window_sec': 60,
        'frames': 30,
        'duration_sec': 12.5,
        'components': [
          {
            'component_id': 'pump',
            'frames_seen': 30,
            'frames_total': 30,
            'visibility_ratio': 1.0,
            'defects': [
              {
                'class_name': 'corrosion',
                'confirmed': true,
                'presence_ratio': 0.7,
              },
            ],
          },
        ],
      },
    });
    expect(record.analytics, isNotNull);
    expect(record.analytics!.frames, 30);
    expect(record.analytics!.components.single.confirmedDefects, hasLength(1));
  });
}
