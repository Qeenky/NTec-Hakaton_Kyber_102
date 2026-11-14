import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/frame_result.dart';
import 'package:ntec_inspector/models/inspection.dart';
import 'package:ntec_inspector/models/verdict.dart';

void main() {
  test('verdict: парсинг серверного ответа', () {
    final verdict = InspectionVerdict.fromJsonOrNull(<String, dynamic>{
      'status': 'defects_found',
      'reason': 'На корпусе насоса видны следы коррозии.',
      'components_with_issues': ['pump'],
      'components_not_seen': ['manometer'],
      'disclaimer': 'Отсутствие видимых дефектов не подтверждает исправность оборудования.',
    });

    expect(verdict, isNotNull);
    expect(verdict!.status, 'defects_found');
    expect(verdict.reason, contains('коррозии'));
    expect(verdict.componentsWithIssues, <String>['pump']);
    expect(verdict.componentsNotSeen, <String>['manometer']);
    expect(verdict.disclaimer, contains('не подтверждает'));
    expect(verdict.isGood, isFalse);
  });

  test('verdict: серверный «норма» не скрывает невидимые узлы', () {
    final verdict = InspectionVerdict.fromJson(<String, dynamic>{
      'status': 'no_visible_defects',
      'reason': 'Дефектов нет.',
      'components_with_issues': <dynamic>[],
      'components_not_seen': ['manometer'],
    });
    expect(verdict.status, 'insufficient_data');
    expect(verdict.isGood, isFalse);
    expect(verdict.componentsNotSeen, contains('manometer'));
  });

  test('verdict: fallback — проблемы важнее нормы', () {
    final verdict = InspectionVerdict.resolve(
      roster: ['pump', 'motor', 'manometer'],
      observations: const [
        VerdictObservation(
          componentId: 'pump',
          status: 'possible_defect',
          seen: true,
        ),
        VerdictObservation(
          componentId: 'motor',
          status: 'ok',
          seen: true,
        ),
      ],
    );
    expect(verdict.status, 'attention_required');
    expect(verdict.componentsWithIssues, contains('pump'));
    expect(verdict.componentsNotSeen, contains('manometer'));
    expect(verdict.disclaimer, isNotEmpty);
  });

  test('verdict: fallback — нет данных важнее «нормы»', () {
    final insufficient = InspectionVerdict.resolve(
      roster: ['pump', 'manometer'],
      observations: const [
        VerdictObservation(
          componentId: 'pump',
          status: 'ok',
          seen: true,
        ),
        VerdictObservation(
          componentId: 'manometer',
          status: 'insufficient_data',
          seen: false,
        ),
      ],
    );
    expect(insufficient.status, 'insufficient_data');
    expect(insufficient.componentsNotSeen, contains('manometer'));

    final noFrame = InspectionVerdict.resolve(
      roster: ['pump'],
      observations: const [],
      hasFrame: false,
    );
    expect(noFrame.status, 'insufficient_data');
    expect(noFrame.componentsNotSeen, contains('pump'));

    final empty = InspectionVerdict.resolve(
      roster: const <String>[],
      observations: const [],
    );
    expect(empty.status, 'insufficient_data');
    expect(empty.isGood, isFalse);
  });

  test('verdict: fallback — все узлы ok и видны', () {
    final verdict = InspectionVerdict.resolve(
      roster: ['pump', 'motor'],
      observations: const [
        VerdictObservation(componentId: 'pump', status: 'ok'),
        VerdictObservation(componentId: 'motor', status: 'ok'),
      ],
    );
    expect(verdict.status, 'no_visible_defects');
    expect(verdict.isGood, isTrue);
    expect(verdict.componentsWithIssues, isEmpty);
    expect(verdict.componentsNotSeen, isEmpty);
  });

  test('verdict: fallback — видимый дефект не даёт «норму»', () {
    final verdict = InspectionVerdict.resolve(
      roster: ['pump'],
      observations: const [
        VerdictObservation(componentId: 'pump', status: 'ok'),
      ],
      hasDefectEvidence: true,
    );
    expect(verdict.status, 'attention_required');
    expect(verdict.isGood, isFalse);
  });

  test('FrameResult.resolveVerdict: учитывает статусы и видимость', () {
    final frame = FrameResult.fromJson(<String, dynamic>{
      'image_width': 1280,
      'image_height': 960,
      'board': {'detected': true, 'markers': 4},
      'components_status': [
        {'component_id': 'pump', 'status': 'check_required'},
        {'component_id': 'motor', 'status': 'ok'},
        {'component_id': 'manometer', 'status': 'insufficient_data'},
      ],
      'overlays': <dynamic>[],
      'detections': <dynamic>[],
      'active_diagnostics': <dynamic>[],
      'blocked_diagnostics': <dynamic>[],
    });
    final verdict = frame.resolveVerdict(<String>['pump', 'motor', 'manometer']);
    expect(verdict.status, 'attention_required');
    expect(verdict.componentsWithIssues, contains('pump'));
    expect(verdict.componentsNotSeen, contains('manometer'));
  });

  test('InspectionRecord: серверный verdict и fallback из сохранённого осмотра',
      () {
    final withServer = InspectionRecord.fromJson(<String, dynamic>{
      'id': 10,
      'equipment_id': 'PUMP-001',
      'created_at': '2026-11-13T12:00:00+00:00',
      'summary': {
        'components_status': <dynamic>[],
        'detections': <dynamic>[],
        'diagnostics': <dynamic>[],
      },
      'diagnostics': <dynamic>[],
      'verdict': {
        'status': 'no_visible_defects',
        'reason': 'На видимых узлах дефектов нет.',
        'components_with_issues': <dynamic>[],
        'components_not_seen': <dynamic>[],
      },
    });
    expect(withServer.verdict?.status, 'no_visible_defects');
    expect(withServer.effectiveVerdict().status, 'no_visible_defects');

    final withoutServer = InspectionRecord.fromJson(<String, dynamic>{
      'id': 11,
      'equipment_id': 'PUMP-001',
      'created_at': '2026-11-13T12:00:00+00:00',
      'summary': {
        'components_status': [
          {'component_id': 'pump', 'status': 'ok'},
          {'component_id': 'manometer', 'status': 'insufficient_data'},
        ],
        'detections': <dynamic>[],
        'diagnostics': <dynamic>[],
      },
      'diagnostics': <dynamic>[],
    });
    final fallback = withoutServer.effectiveVerdict(
      roster: <String>['pump', 'manometer'],
    );
    expect(withoutServer.verdict, isNull);
    expect(fallback.status, 'insufficient_data');
    expect(fallback.componentsNotSeen, contains('manometer'));
  });
}
