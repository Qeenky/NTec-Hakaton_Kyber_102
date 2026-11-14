import 'dart:math' as math;

import 'package:flutter/painting.dart';

/// Пересчёт координат кадра в экранные с учётом BoxFit.contain/cover.
class OverlayMapping {
  const OverlayMapping({required this.scale, required this.offset});

  final double scale;
  final Offset offset;

  factory OverlayMapping.forFit({
    required Size viewport,
    required Size frame,
    required BoxFit fit,
  }) {
    if (frame.width <= 0 || frame.height <= 0) {
      return const OverlayMapping(scale: 1, offset: Offset.zero);
    }
    final scaleX = viewport.width / frame.width;
    final scaleY = viewport.height / frame.height;
    final scale = fit == BoxFit.contain
        ? math.min(scaleX, scaleY)
        : math.max(scaleX, scaleY);
    final offset = Offset(
      (viewport.width - frame.width * scale) / 2,
      (viewport.height - frame.height * scale) / 2,
    );
    return OverlayMapping(scale: scale, offset: offset);
  }

  Offset toScreen(List<double> point) => Offset(
        point[0] * scale + offset.dx,
        point[1] * scale + offset.dy,
      );

  Offset toFrame(Offset point) => Offset(
        (point.dx - offset.dx) / scale,
        (point.dy - offset.dy) / scale,
      );
}
