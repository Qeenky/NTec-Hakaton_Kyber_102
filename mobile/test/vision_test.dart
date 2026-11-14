import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/frame_result.dart';
import 'package:ntec_inspector/models/labels.dart';
import 'package:ntec_inspector/models/vision.dart';

void main() {
  test('VisionCheck: вердикт, признаки, null → «не видно»', () {
    final check = VisionCheck.fromJson(<String, dynamic>{
      'component_id': 'pump',
      'verdict': 'defect',
      'signs': {
        'corrosion': true,
        'paint_peeling': false,
        'oil_leak': null,
        'mechanical_damage': true,
      },
      'confidence': 0.8,
      'comment': 'На корпусе видны следы коррозии.',
      'surface': 'Корпус насоса',
      'model': 'qwen2.5vl:3b',
      'latency_ms': 1500,
      'grounded': true,
      'error': null,
      'cached': false,
    });

    expect(check.componentId, 'pump');
    expect(check.verdict, 'defect');
    expect(check.isDefect, isTrue);
    expect(check.hasDefectSigns, isTrue);
    expect(check.signs.corrosion, isTrue);
    expect(check.signs.paintPeeling, isFalse);
    expect(check.signs.oilLeak, isNull);
    expect(check.confidence, 0.8);
    expect(check.latencyMs, 1500);
    expect(check.model, 'qwen2.5vl:3b');
    expect(check.hasError, isFalse);
    expect(visionSignValueLabel(check.signs.oilLeak), 'не видно');
    expect(visionSignValueLabel(check.signs.corrosion), 'есть');
    expect(visionSignValueLabel(check.signs.paintPeeling), 'нет');
    expect(visionVerdictLabel(check.verdict),
        'Обнаружены признаки дефекта');
    expect(visionSignLabel('mechanical_damage'), 'Механическое повреждение');
  });

  test('VisionCheck: ошибка модели не роняет парсинг', () {
    final check = VisionCheck.fromJson(<String, dynamic>{
      'component_id': 'motor',
      'verdict': 'insufficient',
      'signs': <String, dynamic>{},
      'confidence': 0.0,
      'latency_ms': 0,
      'grounded': false,
      'error': 'model_unavailable',
      'cached': false,
    });
    expect(check.hasError, isTrue);
    expect(check.signs.corrosion, isNull);
    expect(check.verdict, 'insufficient');
    expect(visionVerdictLabel(check.verdict), 'Недостаточно данных');
  });

  test('VisionExplain: summary, actions, severity', () {
    final explain = VisionExplain.fromJson(<String, dynamic>{
      'summary': 'Насос требует внимания: обнаружена коррозия.',
      'severity': 'warning',
      'actions': ['Проверить корпус', 'Запланировать окраску'],
      'facts_used': ['visual_check', 'diagnostics'],
      'model': 'qwen2.5vl:3b',
      'latency_ms': 2647,
      'error': null,
    });

    expect(explain.summary, contains('коррозия'));
    expect(explain.severity, 'warning');
    expect(explain.actions, hasLength(2));
    expect(explain.factsUsed, hasLength(2));
    expect(explain.model, 'qwen2.5vl:3b');
    expect(explain.latencyMs, 2647);
    expect(explain.hasError, isFalse);
  });

  test('VisionExplain: error → недоступно', () {
    final explain = VisionExplain.fromJson(<String, dynamic>{
      'summary': '',
      'severity': 'info',
      'actions': <dynamic>[],
      'facts_used': <dynamic>[],
      'error': 'connect_error',
    });
    expect(explain.hasError, isTrue);
    expect(explain.isEmpty, isTrue);
  });

  test('FrameResult: vision из /analyze подхватывается', () {
    final frame = FrameResult.fromJson(<String, dynamic>{
      'image_width': 1280,
      'image_height': 960,
      'board': {'detected': true, 'markers': 4},
      'overlays': [
        {
          'component_id': 'pump',
          'label': 'Насос',
          'polygon': [
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 10],
          ],
          'visible': true,
          'status': 'ok',
          'color': 'green',
        },
      ],
      'vision': {
        'component_id': 'pump',
        'verdict': 'no_defect',
        'signs': {'corrosion': false},
        'confidence': 0.8,
        'latency_ms': 2000,
        'grounded': true,
        'error': null,
        'cached': true,
      },
    });
    expect(frame.vision, isNotNull);
    expect(frame.vision!.componentId, 'pump');
    expect(frame.vision!.verdict, 'no_defect');
    expect(frame.vision!.cached, isTrue);
    expect(frame.vision!.signs.corrosion, isFalse);
    expect(frame.vision!.signs.oilLeak, isNull);
  });
}
