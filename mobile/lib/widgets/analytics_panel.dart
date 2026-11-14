import 'dart:async';

import 'package:material_ui/material_ui.dart';

import '../models/analytics.dart';
import '../services/frame_socket.dart';
import '../theme/app_theme.dart';
import 'visual_signs.dart';

String analyticsStatusLabel(AnalyticsComponent component) {
  if (component.outOfFrame) return 'Не в кадре';
  switch (component.statusWindow) {
    case 'possible_defect':
      return 'Возможный дефект';
    case 'check_required':
      return 'Требует внимания';
    case 'ok':
      return 'Норма';
    case 'insufficient_data':
      return 'Нет данных';
    default:
      return component.statusWindow;
  }
}

String analyticsShortStatusLabel(AnalyticsComponent component) {
  if (component.outOfFrame) return 'Не в кадре';
  switch (component.statusWindow) {
    case 'possible_defect':
      return 'Дефект';
    case 'check_required':
      return 'Внимание';
    case 'ok':
      return 'Норма';
    case 'insufficient_data':
      return 'Нет данных';
    default:
      return component.statusWindow;
  }
}

Color analyticsStatusColor(
  AnalyticsComponent component, [
  AppPalette? palette,
]) {
  final p = palette ?? AppPalette.dark;
  if (component.outOfFrame) return p.grey;
  return p.byName(component.statusWindow);
}

/// Бейдж статуса окна для панели и таблицы.
class AnalyticsStatusBadge extends StatelessWidget {
  const AnalyticsStatusBadge({
    super.key,
    required this.component,
    this.short = false,
  });

  final AnalyticsComponent component;
  final bool short;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = analyticsStatusColor(component, palette);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(
        short
            ? analyticsShortStatusLabel(component)
            : analyticsStatusLabel(component),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

/// Сворачиваемая панель поузловой видеоаналитики на AR-экране.
///
/// Слушает [FrameSocket] через таймер: не чаще 2 раз в секунду и перерисовывает
/// только себя, не весь экран.
class AnalyticsPanel extends StatefulWidget {
  const AnalyticsPanel({
    super.key,
    required this.socket,
    required this.roster,
    this.initiallyExpanded = true,
  });

  final FrameSocket socket;
  final List<String> roster;
  final bool initiallyExpanded;

  @override
  State<AnalyticsPanel> createState() => _AnalyticsPanelState();
}

class _AnalyticsPanelState extends State<AnalyticsPanel> {
  static const Duration _updatePeriod = Duration(milliseconds: 500);

  Timer? _timer;
  FrameAnalytics? _analytics;
  String _signature = '';
  late bool _expanded = widget.initiallyExpanded;

  @override
  void initState() {
    super.initState();
    _pull();
    _timer = Timer.periodic(_updatePeriod, (_) => _pull());
  }

  @override
  void didUpdateWidget(covariant AnalyticsPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.socket != widget.socket) {
      _pull(force: true);
    }
  }

  void _pull({bool force = false}) {
    if (!mounted) return;
    final analytics = widget.socket.lastFrame?.analytics;
    final signature = analytics?.signature() ?? '';
    if (!force && signature == _signature) return;
    setState(() {
      _analytics = analytics;
      _signature = signature;
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final analytics = _analytics;
    if (analytics == null || analytics.isEmpty) {
      return const SizedBox.shrink();
    }
    final rows = analytics.withRoster(widget.roster);
    if (rows.isEmpty) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(11),
        border: Border.all(color: AppColors.outline),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: BorderRadius.circular(11),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 8, 8),
              child: Row(
                children: [
                  const Icon(
                    Icons.insights_outlined,
                    size: 16,
                    color: AppColors.accent,
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: Text(
                      analytics.headerText,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: palette.text,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  if (!_expanded)
                    Text(
                      '${rows.where((item) => item.statusWindow == 'possible_defect').length} с дефектами',
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  Icon(
                    _expanded ? Icons.expand_more : Icons.expand_less,
                    size: 18,
                    color: AppColors.textMuted,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded)
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 235),
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(10, 0, 10, 6),
                child: Column(
                  children: [
                    for (var index = 0; index < rows.length; index++) ...[
                      _PanelRow(component: rows[index]),
                      if (index < rows.length - 1)
                        const Divider(
                          color: AppColors.outline,
                          height: 1,
                          thickness: 0.6,
                        ),
                    ],
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _PanelRow extends StatelessWidget {
  const _PanelRow({required this.component});

  final AnalyticsComponent component;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = analyticsStatusColor(component);
    final defects = component.confirmedDefects;
    final shownSigns = component.signs
        .where((sign) => sign.isShown)
        .toList(growable: false);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 3,
            height: 30,
            margin: const EdgeInsets.only(top: 2),
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        component.label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    AnalyticsStatusBadge(component: component),
                  ],
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    Expanded(
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(2),
                        child: LinearProgressIndicator(
                          value: component.visibilityRatio,
                          minHeight: 4,
                          backgroundColor: AppColors.outline,
                          valueColor: AlwaysStoppedAnimation<Color>(color),
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    SizedBox(
                      width: 34,
                      child: Text(
                        '${(component.visibilityRatio * 100).round()}%',
                        textAlign: TextAlign.right,
                        style: const TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 12,
                        ),
                      ),
                    ),
                  ],
                ),
                if (shownSigns.isNotEmpty)
                  VisualSignsInline(signs: component.signs)
                else if (defects.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Row(
                      children: [
                        const Icon(
                          Icons.warning_amber_rounded,
                          size: 12,
                          color: AppColors.red,
                        ),
                        const SizedBox(width: 4),
                        Expanded(
                          child: Text(
                            defects.map((item) => item.label).join(', '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: AppColors.red,
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
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

/// Таблица видеоаналитики для экрана результата и вкладки «Анализ».
class AnalyticsTable extends StatelessWidget {
  const AnalyticsTable({
    super.key,
    required this.analytics,
    required this.roster,
  });

  final FrameAnalytics analytics;
  final List<String> roster;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final rows = analytics.withRoster(roster);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'УЗЕЛ',
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.4,
                ),
              ),
            ),
            SizedBox(
              width: 86,
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
              width: 52,
              child: Text(
                'ВИДНО',
                textAlign: TextAlign.right,
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.4,
                ),
              ),
            ),
            const SizedBox(width: 8),
            SizedBox(
              width: 86,
              child: Text(
                'ДЕФЕКТЫ',
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
        for (final component in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        component.label,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${component.framesSeen}/${component.framesTotal} кадров',
                        style: TextStyle(
                          color: palette.textMuted,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
                SizedBox(
                  width: 86,
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: AnalyticsStatusBadge(
                      component: component,
                      short: true,
                    ),
                  ),
                ),
                SizedBox(
                  width: 52,
                  child: Text(
                    '${(component.visibilityRatio * 100).round()}%',
                    textAlign: TextAlign.right,
                    style: TextStyle(
                      color: analyticsStatusColor(component, palette),
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: 86,
                  child: Text(
                    component.confirmedDefects.isEmpty
                        ? '—'
                        : component.confirmedDefects
                              .map((item) => item.label)
                              .join(', '),
                    style: TextStyle(
                      color: component.confirmedDefects.isEmpty
                          ? palette.textMuted
                          : palette.red,
                      fontSize: 12,
                      height: 1.25,
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
