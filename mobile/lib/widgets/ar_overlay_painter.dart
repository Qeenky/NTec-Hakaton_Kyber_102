import 'dart:math' as math;

import 'package:material_ui/material_ui.dart';

import '../models/frame_result.dart';
import '../theme/app_theme.dart';
import 'overlay_mapping.dart';

/// Рисует полигоны `/ws/frame` поверх превью с учётом `BoxFit.cover`.
class ArOverlayPainter extends CustomPainter {
  ArOverlayPainter({
    required this.frame,
    this.clipBottom = 0,
    this.fit = BoxFit.cover,
    this.hideOverlays = false,
  });

  final FrameResult? frame;

  /// Высота нижней панели: подписи и полигоны не должны заходить под кнопки.
  final double clipBottom;

  /// Для камеры — cover, для загруженного фото — contain (letterbox).
  final BoxFit fit;

  /// Полигоны скрыты (нет привязки дольше 1.5 с).
  final bool hideOverlays;

  @override
  void paint(Canvas canvas, Size size) {
    final result = frame;
    if (result == null ||
        !result.hasFrameSize ||
        size.width <= 0 ||
        size.height <= 0) {
      return;
    }

    final visibleHeight = math.max(0.0, size.height - clipBottom);
    if (visibleHeight <= 0) return;
    final bounds = Size(size.width, visibleHeight);
    canvas.save();
    canvas.clipRect(Rect.fromLTWH(0, 0, size.width, visibleHeight));

    final zones = result.drawableOverlays(hideOverlays: hideOverlays);
    if (zones.isEmpty) {
      canvas.restore();
      return;
    }
    final stale = result.board.stale;
    final opacity = stale ? 0.3 : 1.0;

    final frameWidth = result.imageWidth.toDouble();
    final frameHeight = result.imageHeight.toDouble();
    final viewport = fit == BoxFit.contain
        ? Size(size.width, visibleHeight)
        : size;
    final mapping = OverlayMapping.forFit(
      viewport: viewport,
      frame: Size(frameWidth, frameHeight),
      fit: fit,
    );

    Offset mapPoint(List<double> point) => mapping.toScreen(point);

    for (final zone in zones) {
      if (!zone.hasGeometry) continue;
      final points = zone.polygon.map(mapPoint).toList(growable: false);
      final path = Path()..addPolygon(points, true);
      final color = AppColors.byName(zone.color);

      if (!stale) {
        canvas.drawPath(
          path,
          Paint()
            ..style = PaintingStyle.fill
            ..color = color.withValues(alpha: 0.13),
        );
      }
      final strokePaint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = stale ? 1.2 : 2.5
        ..strokeJoin = StrokeJoin.round
        ..color = color.withValues(alpha: opacity * (stale ? 0.9 : 0.95));
      if (stale) {
        _drawDashedPath(canvas, path, strokePaint);
      } else {
        canvas.drawPath(path, strokePaint);
        _drawTag(
          canvas,
          bounds,
          points.reduce(_topLeft),
          '${zone.label} · ${_statusShort(zone.status)}',
          color,
        );
      }
    }

    if (!stale) {
      for (final diagnostic in result.activeDiagnostics) {
        final overlay = diagnostic.overlay;
        final componentId = overlay?.componentId;
        if (componentId == null) continue;
        OverlayZone? zone;
        for (final candidate in zones) {
          if (candidate.componentId == componentId) {
            zone = candidate;
            break;
          }
        }
        if (zone == null || !zone.hasGeometry) continue;
        final points = zone.polygon.map(mapPoint).toList(growable: false);
        final color = AppColors.byName(overlay?.color ?? 'orange');
        canvas.drawPath(
          Path()..addPolygon(points, true),
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 4
            ..strokeJoin = StrokeJoin.round
            ..color = color,
        );
        final label = overlay?.label ?? diagnostic.title;
        _drawTag(
          canvas,
          bounds,
          points.reduce(_topLeft),
          label,
          color,
          strong: true,
        );
      }
    }
    canvas.restore();
  }

  void _drawDashedPath(Canvas canvas, Path path, Paint paint) {
    const dash = 8.0;
    const gap = 6.0;
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = math.min(distance + dash, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), paint);
        distance = next + gap;
      }
    }
  }

  Offset _topLeft(Offset a, Offset b) {
    if (a.dy < b.dy - 1) return a;
    if (b.dy < a.dy - 1) return b;
    return a.dx <= b.dx ? a : b;
  }

  void _drawTag(
    Canvas canvas,
    Size size,
    Offset anchor,
    String text,
    Color color, {
    bool strong = false,
  }) {
    if (text.trim().isEmpty) return;
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color.computeLuminance() > 0.55
              ? const Color(0xFF0B0F14)
              : Colors.white,
          fontSize: strong ? 13 : 12,
          fontWeight: FontWeight.w700,
        ),
      ),
      maxLines: 1,
      ellipsis: '…',
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: math.max(60, size.width - 16));

    final paddingX = 7.0;
    final paddingY = 4.0;
    final tagWidth = painter.width + paddingX * 2;
    final tagHeight = painter.height + paddingY * 2;
    var left = anchor.dx;
    var top = anchor.dy - tagHeight - 6;
    left = left.clamp(4.0, math.max(4.0, size.width - tagWidth - 4)).toDouble();
    if (top < 4) top = anchor.dy + 6;
    top = top.clamp(4.0, math.max(4.0, size.height - tagHeight - 4)).toDouble();

    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(left, top, tagWidth, tagHeight),
      const Radius.circular(5),
    );
    canvas.drawRRect(
      rect,
      Paint()..color = color.withValues(alpha: strong ? 0.98 : 0.92),
    );
    canvas.drawRRect(
      rect,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = Colors.black.withValues(alpha: 0.35),
    );
    painter.paint(canvas, Offset(left + paddingX, top + paddingY));
  }

  String _statusShort(String status) {
    switch (status) {
      case 'ok':
        return 'норма';
      case 'check_required':
        return 'проверить';
      case 'possible_defect':
        return 'дефект';
      case 'insufficient_data':
        return 'нет данных';
      default:
        return status;
    }
  }

  @override
  bool shouldRepaint(covariant ArOverlayPainter oldDelegate) =>
      !identical(oldDelegate.frame, frame) ||
      oldDelegate.clipBottom != clipBottom ||
      oldDelegate.fit != fit ||
      oldDelegate.hideOverlays != hideOverlays;
}
