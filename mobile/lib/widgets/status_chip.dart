import 'package:material_ui/material_ui.dart';

import '../models/labels.dart';
import '../theme/app_theme.dart';

/// Цветная плашка статуса: «Норма», «Требует внимания», «Возможный дефект», «Нет данных».
/// Всегда кодирует статус цветом + иконкой/формой + словом.
class StatusChip extends StatelessWidget {
  const StatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.dense = false,
  });

  /// Плашка по коду статуса из контракта (`ok`, `possible_defect`, ...).
  factory StatusChip.status({
    Key? key,
    required String status,
    String? label,
    bool dense = false,
  }) {
    final icon = switch (status) {
      'ok' || 'green' || 'no_visible_defects' => Icons.check_circle_outline,
      'check_required' ||
      'attention_required' ||
      'warning' ||
      'yellow' =>
        Icons.warning_amber_rounded,
      'possible_defect' ||
      'defects_found' ||
      'critical' ||
      'defect' ||
      'red' =>
        Icons.error_outline,
      _ => Icons.help_outline,
    };
    return StatusChip(
      key: key,
      label: label ?? componentStatusLabel(status),
      color: AppColors.byName(status),
      icon: icon,
      dense: dense,
    );
  }

  final String label;
  final Color color;
  final IconData? icon;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final effectiveColor = palette.adaptColor(color);
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? 8 : 10,
        vertical: dense ? 4 : 5,
      ),
      decoration: BoxDecoration(
        color: effectiveColor.withValues(alpha: palette.isDark ? 0.16 : 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: effectiveColor.withValues(alpha: 0.55)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: effectiveColor),
            const SizedBox(width: 5),
          ] else ...[
            Container(
              width: dense ? 6 : 8,
              height: dense ? 6 : 8,
              decoration: BoxDecoration(
                color: effectiveColor,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 6),
          ],
          Text(
            label,
            style: TextStyle(
              color: effectiveColor,
              fontSize: dense ? 12 : 13,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.1,
            ),
          ),
        ],
      ),
    );
  }
}
