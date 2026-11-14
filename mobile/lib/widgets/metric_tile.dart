import 'package:material_ui/material_ui.dart';

import '../theme/app_theme.dart';

/// Крупная метрика: подпись сверху, табличное значение и единица снизу.
class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.unit,
    this.color,
    this.caption,
    this.compact = false,
  });

  final String label;
  final String value;
  final String? unit;
  final Color? color;
  final String? caption;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final valueColor = color == null ? palette.text : palette.adaptColor(color!);
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: palette.surfaceHigh,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Flexible(
                child: Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: valueColor,
                    fontSize: compact ? 22 : 28,
                    fontWeight: FontWeight.w800,
                    height: 1.1,
                    letterSpacing: -0.5,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
              ),
              if (unit != null && unit!.isNotEmpty) ...[
                const SizedBox(width: AppSpacing.xs),
                Text(
                  unit!,
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ],
          ),
          if (caption != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              caption!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
