import 'package:material_ui/material_ui.dart';

import '../models/diagnostic.dart';
import '../models/labels.dart';
import '../theme/app_theme.dart';
import 'status_chip.dart';

/// Плашка диагностики: severity-полоса 4 px слева, заголовок, evidence, рекомендация.
class DiagnosticCard extends StatelessWidget {
  const DiagnosticCard({
    super.key,
    required this.event,
    this.showEvidence = false,
    this.compact = false,
  });

  final DiagnosticEvent event;
  final bool showEvidence;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = palette.byName(event.severity);
    final confidence = event.confidence;
    final isDefect = event.isCritical || event.severity == 'defect';
    final bgColor = isDefect
        ? (palette.isDark ? const Color(0xFF1A1215) : palette.errorSurface)
        : palette.surface;

    return Container(
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(16),
        border: Border(
          left: BorderSide(color: color, width: 4),
          top: BorderSide(color: palette.outline),
          right: BorderSide(color: palette.outline),
          bottom: BorderSide(color: palette.outline),
        ),
      ),
      padding: EdgeInsets.fromLTRB(12, compact ? 12 : 16, 16, compact ? 12 : 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                event.isCritical
                    ? Icons.warning_amber_rounded
                    : Icons.info_outline_rounded,
                color: color,
                size: 20,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  event.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.text,
                    fontSize: compact ? 15 : 16,
                    fontWeight: FontWeight.w700,
                    height: 1.25,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              StatusChip(
                label: severityLabel(event.severity),
                color: color,
                dense: true,
              ),
            ],
          ),
          if (event.message != null && event.message!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              event.message!,
              maxLines: compact ? 2 : 3,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.textSecondary,
                fontSize: compact ? 13 : 14,
                height: 1.35,
              ),
            ),
          ],
          if (event.recommendation != null &&
              event.recommendation!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '→ ',
                  style: TextStyle(
                    color: palette.primary,
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                Expanded(
                  child: Text(
                    event.recommendation!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: palette.text,
                      fontSize: compact ? 13.5 : 14.5,
                      fontWeight: FontWeight.w600,
                      height: 1.35,
                    ),
                  ),
                ),
              ],
            ),
          ],
          if (confidence != null && confidence > 0) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Text(
                  'Достоверность',
                  style: TextStyle(color: palette.textMuted, fontSize: 12),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: confidence.clamp(0, 1),
                      minHeight: 5,
                      backgroundColor: palette.outline,
                      valueColor: AlwaysStoppedAnimation<Color>(color),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '${(confidence * 100).round()}%',
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
          ],
          if (showEvidence && event.evidence.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              'ФАКТЫ (EVIDENCE)',
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.5,
              ),
            ),
            const SizedBox(height: 4),
            for (final item in event.evidence)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      item.matched
                          ? Icons.check_circle_outline
                          : Icons.remove_circle_outline,
                      size: 16,
                      color: item.matched ? color : palette.grey,
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        item.description,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 13,
                          height: 1.35,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}
