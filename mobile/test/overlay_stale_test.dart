import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/frame_result.dart';

FrameResult _frame({
  required bool detected,
  required bool stale,
  required List<List<double>> polygon,
}) {
  return FrameResult.fromJson(<String, dynamic>{
    'frame_id': 12,
    'image_width': 100,
    'image_height': 100,
    'board': {
      'detected': detected,
      'stale': stale,
      'markers': detected ? 4 : 0,
      'reason': detected ? null : 'no_markers',
    },
    'overlays': [
      {
        'component_id': 'pump',
        'label': 'Насос',
        'polygon': polygon,
        'visible': true,
        'status': 'ok',
        'color': 'green',
      },
    ],
    'components_status': <dynamic>[],
    'detections': <dynamic>[],
    'active_diagnostics': <dynamic>[],
    'blocked_diagnostics': <dynamic>[],
  });
}

void main() {
  test('board.detected=false → оверлеи не рисуются', () {
    final frame = _frame(
      detected: false,
      stale: false,
      polygon: [
        [10, 10],
        [90, 10],
        [90, 90],
        [10, 90],
      ],
    );
    expect(frame.drawableOverlays(), isEmpty);
  });

  test('board.stale=true → прозрачность 30%, полигоны остаются', () {
    final frame = _frame(
      detected: true,
      stale: true,
      polygon: [
        [10, 10],
        [90, 10],
        [90, 90],
        [10, 90],
      ],
    );
    expect(frame.overlayOpacity, 0.3);
    expect(frame.drawableOverlays(), hasLength(1));
  });

  test('узел, вышедший за кадр больше чем на 20%, не рисуется', () {
    final frame = _frame(
      detected: true,
      stale: false,
      polygon: [
        [-80, 10],
        [20, 10],
        [20, 90],
        [-80, 90],
      ],
    );
    expect(frame.drawableOverlays(), isEmpty);
  });

  test('свежая поза → обычная прозрачность', () {
    final frame = _frame(
      detected: true,
      stale: false,
      polygon: [
        [10, 10],
        [90, 10],
        [90, 90],
        [10, 90],
      ],
    );
    expect(frame.overlayOpacity, 1.0);
    expect(frame.drawableOverlays(), hasLength(1));
  });
}
