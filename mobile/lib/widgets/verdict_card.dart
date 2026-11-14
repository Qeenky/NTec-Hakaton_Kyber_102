import 'package:material_ui/material_ui.dart';

import '../models/labels.dart';
import '../models/verdict.dart';
import '../theme/app_theme.dart';

String verdictTitle(String status) {
  switch (status) {
    case 'defects_found':
      return 'Обнаружены признаки дефекта';
    case 'attention_required':
      return 'Требуется проверка';
    case 'insufficient_data':
      return 'Недостаточно данных для вывода';
    case 'no_visible_defects':
      return 'Видимых дефектов не обнаружено';
    default:
      return 'Недостаточно данных для вывода';
  }
}

String verdictShortTitle(String status) {
  switch (status) {
    case 'defects_found':
      return 'Дефекты';
    case 'attention_required':
      return 'Проверка';
    case 'no_visible_defects':
      return 'Норма';
    default:
      return 'Нет данных';
  }
}

Color verdictColor(String status, [AppPalette? palette]) {
  final p = palette ?? AppPalette.dark;
  switch (status) {
    case 'defects_found':
      return p.red;
    case 'attention_required':
      return p.yellow;
    case 'no_visible_defects':
      return p.green;
    default:
      return p.grey;
  }
}

IconData verdictIcon(String status) {
  switch (status) {
    case 'defects_found':
      return Icons.error_outline;
    case 'attention_required':
      return Icons.warning_amber_rounded;
    case 'no_visible_defects':
      return Icons.verified_outlined;
    default:
      return Icons.help_outline;
  }
}

/// Компактный бейдж итога для списка истории.
class VerdictBadge extends StatelessWidget {
  const VerdictBadge({super.key, required this.verdict});

  final InspectionVerdict verdict;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = verdictColor(verdict.status, palette);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: palette.isDark ? 0.16 : 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.55)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(verdictIcon(verdict.status), size: 13, color: color),
          const SizedBox(width: 5),
          Text(
            verdictShortTitle(verdict.status),
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

/// Итоговый блок осмотра: статус, причина, узлы и обязательный дисклеймер.
class VerdictCard extends StatelessWidget {
  const VerdictCard({
    super.key,
    required this.verdict,
    this.compact = false,
  });

  final InspectionVerdict verdict;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = verdictColor(verdict.status, palette);
    final issues = verdict.componentsWithIssues;
    final notSeen = verdict.componentsNotSeen;
    return Container(
      padding: EdgeInsets.all(compact ? 12 : 16),
      decoration: BoxDecoration(
        color: color.withValues(alpha: palette.isDark ? 0.08 : 0.06),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: 0.55)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(verdictIcon(verdict.status), color: color, size: compact ? 20 : 22),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  verdictTitle(verdict.status),
                  style: TextStyle(
                    color: color,
                    fontSize: compact ? 16 : 18,
                    fontWeight: FontWeight.w800,
                    height: 1.2,
                  ),
                ),
              ),
            ],
          ),
          if (verdict.reason.isNotEmpty) ...[
            SizedBox(height: compact ? 6 : 8),
            Text(
              verdict.reason,
              style: TextStyle(
                color: palette.text,
                fontSize: 13.5,
                height: 1.35,
              ),
            ),
          ],
          if (issues.isNotEmpty) ...[
            const SizedBox(height: 8),
            _ListLine(
              icon: Icons.error_outline,
              color: palette.red,
              text: 'Проблемные узлы: ${issues.map(componentLabel).join(', ')}',
            ),
          ],
          if (notSeen.isNotEmpty) ...[
            const SizedBox(height: 5),
            _ListLine(
              icon: Icons.visibility_off_outlined,
              color: palette.textMuted,
              text:
                  'Не были видны в кадре: ${notSeen.map(componentLabel).join(', ')}',
            ),
          ],
          const SizedBox(height: 10),
          Divider(color: palette.outline, height: 1),
          const SizedBox(height: 6),
          Text(
            verdict.disclaimer.isEmpty ? verdictDisclaimer : verdict.disclaimer,
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              height: 1.3,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
      ),
    );
  }
}

class _ListLine extends StatelessWidget {
  const _ListLine({
    required this.icon,
    required this.color,
    required this.text,
  });

  final IconData icon;
  final Color color;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 15, color: color),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            text,
            style: TextStyle(
              color: color,
              fontSize: 12.5,
              height: 1.3,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    );
  }
}
