import 'package:material_ui/material_ui.dart';

import '../models/analytics.dart';
import '../theme/app_theme.dart';

String visualSignStatusLabel(String status) {
  switch (status) {
    case 'detected':
      return 'обнаружено';
    case 'single_observation':
      return 'единичное';
    case 'not_visible':
      return 'не видно';
    case 'not_detected':
      return 'не обнаружено';
    default:
      return status;
  }
}

Color visualSignColor(VisualSign sign, [AppPalette? palette]) {
  final p = palette ?? AppPalette.dark;
  if (sign.isSingle) return p.grey;
  if (sign.isDetected) {
    switch (sign.sign) {
      case 'crack':
      case 'chip':
      case 'crack_chip':
      case 'guard_missing':
      case 'missing_guard':
      case 'guard_removed':
      case 'mechanical_damage':
      case 'damage':
        return p.red;
      default:
        return p.orange;
    }
  }
  return p.grey;
}

IconData visualSignIcon(String sign) {
  switch (sign) {
    case 'corrosion':
      return Icons.blur_on;
    case 'oil_leak':
      return Icons.water_drop_outlined;
    case 'oxidation':
    case 'contact_oxidation':
    case 'electrical_oxidation':
      return Icons.bolt_outlined;
    case 'paint_peeling':
      return Icons.layers_clear_outlined;
    case 'mechanical_damage':
    case 'damage':
      return Icons.hardware_outlined;
    case 'crack':
    case 'chip':
    case 'crack_chip':
      return Icons.broken_image_outlined;
    case 'guard_missing':
    case 'missing_guard':
    case 'guard_removed':
      return Icons.remove_circle_outline;
    case 'guard_damage':
      return Icons.shield_outlined;
    default:
      return Icons.help_outline;
  }
}

List<VisualSign> shownVisualSigns(Iterable<VisualSign> signs) {
  final result = signs.where((sign) => sign.isShown).toList();
  result.sort((a, b) {
    if (a.isDetected != b.isDetected) return a.isDetected ? -1 : 1;
    return b.confidence.compareTo(a.confidence);
  });
  return result;
}

/// Компактные признаки в строке узла AR-панели.
class VisualSignsInline extends StatelessWidget {
  const VisualSignsInline({super.key, required this.signs});

  final List<VisualSign> signs;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final shown = shownVisualSigns(signs);
    if (shown.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 3),
      child: Wrap(
        spacing: 6,
        runSpacing: 3,
        children: [
          for (final sign in shown)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: visualSignColor(sign, palette).withValues(alpha: 0.14),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                  color: visualSignColor(sign, palette).withValues(alpha: 0.5),
                ),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    visualSignIcon(sign.sign),
                    size: 12,
                    color: visualSignColor(sign, palette),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    sign.label,
                    style: TextStyle(
                      color: visualSignColor(sign, palette),
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (sign.isSingle)
                    Text(
                      ' · единичное',
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Таблица визуальных признаков для результата и карточки узла.
class VisualSignsTable extends StatelessWidget {
  const VisualSignsTable({
    super.key,
    required this.signs,
    this.compact = false,
  });

  final List<VisualSign> signs;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final shown = shownVisualSigns(signs);
    final notVisible = signs.where((sign) => sign.isNotVisible).toList();
    if (shown.isEmpty && notVisible.isEmpty) {
      return const SizedBox.shrink();
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (shown.isNotEmpty) ...[
          Row(
            children: [
              Expanded(
                child: Text(
                  'ПРИЗНАК',
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                  ),
                ),
              ),
              SizedBox(
                width: 82,
                child: Text(
                  'СТАТУС',
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                  ),
                ),
              ),
              SizedBox(
                width: 64,
                child: Text(
                  'УВЕР.',
                  textAlign: TextAlign.right,
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                  ),
                ),
              ),
              SizedBox(
                width: 52,
                child: Text(
                  'КАДРЫ',
                  textAlign: TextAlign.right,
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 5),
          Divider(color: palette.outline, height: 1),
          for (final sign in shown)
            Padding(
              padding: EdgeInsets.symmetric(vertical: compact ? 4 : 5),
              child: Row(
                children: [
                  Expanded(
                    child: Row(
                      children: [
                        Icon(
                          visualSignIcon(sign.sign),
                          size: 14,
                          color: visualSignColor(sign, palette),
                        ),
                        const SizedBox(width: 5),
                        Expanded(
                          child: Text(
                            sign.label,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: palette.text,
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  SizedBox(
                    width: 82,
                    child: Text(
                      visualSignStatusLabel(sign.status),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: visualSignColor(sign, palette),
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  SizedBox(
                    width: 64,
                    child: Text(
                      sign.confidence > 0
                          ? '${(sign.confidence * 100).round()}%'
                          : '—',
                      textAlign: TextAlign.right,
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ),
                  SizedBox(
                    width: 52,
                    child: Text(
                      sign.framesPresent > 0 ? '${sign.framesPresent}' : '—',
                      textAlign: TextAlign.right,
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
        if (notVisible.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 5),
            child: Text(
              'Не проверено: ${notVisible.length} '
              '${_plural(notVisible.length, 'признак', 'признака', 'признаков')} '
              '(не видно)',
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
                fontStyle: FontStyle.italic,
              ),
            ),
          ),
      ],
    );
  }

  String _plural(int count, String one, String few, String many) {
    final mod10 = count % 10;
    final mod100 = count % 100;
    if (mod10 == 1 && mod100 != 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }
}
