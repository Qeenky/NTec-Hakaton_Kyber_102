import 'package:material_ui/material_ui.dart';

import '../models/labels.dart';
import '../models/vision.dart';
import '../theme/app_theme.dart';
import 'status_chip.dart';

Color visionVerdictColor(VisionCheck check, [AppPalette? palette]) {
  final p = palette ?? AppPalette.dark;
  if (check.hasError) return p.orange;
  switch (check.verdict) {
    case 'defect':
      return p.red;
    case 'no_defect':
      return p.green;
    default:
      return p.grey;
  }
}

Color visionSignColor(bool? value, [AppPalette? palette]) {
  final p = palette ?? AppPalette.dark;
  if (value == null) return p.grey;
  return value ? p.red : p.green;
}

IconData visionSignIcon(String key) {
  switch (key) {
    case 'corrosion':
      return Icons.blur_on;
    case 'paint_peeling':
      return Icons.layers_clear_outlined;
    case 'oil_leak':
      return Icons.water_drop_outlined;
    case 'mechanical_damage':
      return Icons.hardware_outlined;
    default:
      return Icons.help_outline;
  }
}

/// Карточка результата `/vision/check`.
class VisionCheckCard extends StatelessWidget {
  const VisionCheckCard({
    super.key,
    required this.check,
    this.onClose,
    this.compact = false,
    this.showNote = true,
  });

  final VisionCheck check;
  final VoidCallback? onClose;
  final bool compact;
  final bool showNote;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = visionVerdictColor(check, palette);
    final signs = <MapEntry<String, bool?>>[
      MapEntry('corrosion', check.signs.corrosion),
      MapEntry('paint_peeling', check.signs.paintPeeling),
      MapEntry('oil_leak', check.signs.oilLeak),
      MapEntry('mechanical_damage', check.signs.mechanicalDamage),
    ];
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
      decoration: BoxDecoration(
        color: palette.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.7)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.auto_awesome, size: 17, color: color),
              const SizedBox(width: 7),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'AI-проверка · ${componentLabel(check.componentId)}',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        color: palette.textMuted,
                        letterSpacing: 0.3,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      check.hasError
                          ? 'Модель не смогла проверить кадр'
                          : visionVerdictLabel(check.verdict),
                      style: TextStyle(
                        color: color,
                        fontSize: compact ? 14 : 16,
                        fontWeight: FontWeight.w800,
                        height: 1.2,
                      ),
                    ),
                  ],
                ),
              ),
              if (onClose != null)
                InkWell(
                  onTap: onClose,
                  customBorder: const CircleBorder(),
                  child: Padding(
                    padding: const EdgeInsets.all(4),
                    child: Icon(Icons.close, size: 18, color: palette.textMuted),
                  ),
                ),
            ],
          ),
          if (check.hasError)
            Padding(
              padding: const EdgeInsets.only(top: 6, right: 4),
              child: Text(
                _visionErrorText(check.error!),
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  height: 1.3,
                ),
              ),
            )
          else ...[
            const SizedBox(height: 8),
            for (final entry in signs)
              Padding(
                padding: const EdgeInsets.only(bottom: 3, right: 4),
                child: Row(
                  children: [
                    Icon(
                      visionSignIcon(entry.key),
                      size: 14,
                      color: palette.textMuted,
                    ),
                    const SizedBox(width: 7),
                    Expanded(
                      child: Text(
                        visionSignLabel(entry.key),
                        style: TextStyle(color: palette.text, fontSize: 12.5),
                      ),
                    ),
                    Text(
                      visionSignValueLabel(entry.value),
                      style: TextStyle(
                        color: visionSignColor(entry.value, palette),
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ),
            if (check.comment != null && check.comment!.isNotEmpty) ...[
              const SizedBox(height: 5),
              Padding(
                padding: const EdgeInsets.only(right: 4),
                child: Text(
                  check.comment!,
                  maxLines: compact ? 4 : 8,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.text,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              ),
            ],
            const SizedBox(height: 6),
            Padding(
              padding: const EdgeInsets.only(right: 4),
              child: Text(
                _metricsText(check),
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                ),
              ),
            ),
          ],
          if (showNote)
            Padding(
              padding: const EdgeInsets.only(top: 5, right: 4),
              child: Text(
                'Локальная модель, работает без интернета',
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ),
        ],
      ),
    );
  }

  String _metricsText(VisionCheck check) {
    final parts = <String>[
      'уверенность ${(check.confidence * 100).round()}%',
      if (check.latencyMs > 0) '${(check.latencyMs / 1000).toStringAsFixed(1)} с',
      if (check.model != null) check.model!,
      if (check.cached) 'из кэша',
    ];
    return parts.join(' · ');
  }

  String _visionErrorText(String error) {
    switch (error) {
      case 'model_unavailable':
      case 'connect_error':
      case 'timeout':
        return 'Локальная модель недоступна. Проверьте Ollama на сервере.';
      case 'no_frame':
        return 'Нет кадра для проверки.';
      default:
        return 'Ошибка модели: $error';
    }
  }
}

/// Карточка успешного `GET /vision/explain`.
class VisionExplainCard extends StatelessWidget {
  const VisionExplainCard({super.key, required this.explain});

  final VisionExplain explain;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = palette.byName(explain.severity);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: color.withValues(alpha: palette.isDark ? 0.07 : 0.05),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: 0.55)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.auto_awesome, color: color, size: 18),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'AI-заключение',
                  style: TextStyle(
                    color: palette.text,
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              StatusChip(
                label: severityLabel(explain.severity),
                color: color,
                dense: true,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            explain.summary.isEmpty
                ? 'Модель не вернула текст заключения'
                : explain.summary,
            style: TextStyle(color: palette.text, fontSize: 14, height: 1.4),
          ),
          if (explain.actions.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(
              'ДЕЙСТВИЯ',
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
              ),
            ),
            const SizedBox(height: 5),
            for (final action in explain.actions)
              Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.arrow_right, size: 18, color: palette.accent),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        action,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 13,
                          height: 1.3,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 8),
          Text(
            [
              'Локальная модель',
              if (explain.model != null) explain.model!,
              if (explain.latencyMs > 0)
                '${(explain.latencyMs / 1000).toStringAsFixed(1)} с',
              if (explain.factsUsed.isNotEmpty)
                'фактов: ${explain.factsUsed.length}',
            ].join(' · '),
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
            ),
          ),
        ],
      ),
    );
  }
}
