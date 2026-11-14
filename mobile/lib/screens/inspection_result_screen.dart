import 'dart:typed_data';

import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/analytics.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/frame_result.dart';
import '../models/inspection.dart';
import '../models/inspection_event.dart';
import '../models/labels.dart';
import '../models/telemetry.dart';
import '../models/verdict.dart';
import '../models/vision.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/diagnostic_card.dart';
import '../widgets/status_chip.dart';
import '../widgets/analytics_panel.dart';
import '../widgets/inspection_journal.dart';
import '../widgets/verdict_card.dart';
import '../widgets/vision_cards.dart';
import '../widgets/visual_signs.dart';

/// Итог осмотра: что найдено, evidence, рекомендации и сохранение в историю.
class InspectionResultScreen extends StatefulWidget {
  const InspectionResultScreen({
    super.key,
    required this.equipment,
    this.frame,
    this.telemetry,
    this.frozenJpeg,
    this.events = const <InspectionEvent>[],
  });

  final Equipment equipment;
  final FrameResult? frame;
  final TelemetryMessage? telemetry;
  final Uint8List? frozenJpeg;
  final List<InspectionEvent> events;

  @override
  State<InspectionResultScreen> createState() => _InspectionResultScreenState();
}

class _InspectionResultScreenState extends State<InspectionResultScreen> {
  bool _saving = false;
  bool _explainStarted = false;
  bool _explainLoading = false;
  VisionExplain? _explain;
  String? _explainError;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_explainStarted) return;
    _explainStarted = true;
    _loadExplain();
  }

  Future<void> _loadExplain() async {
    setState(() {
      _explainLoading = true;
      _explainError = null;
    });
    try {
      final explain = await AppScope.of(context).api
          .visionExplain(widget.equipment.id);
      if (!mounted) return;
      setState(() {
        if (explain.hasError) {
          _explain = null;
          _explainError = explain.error;
        } else {
          _explain = explain;
        }
      });
    } on ApiException catch (exception) {
      if (!mounted) return;
      setState(() {
        _explain = null;
        _explainError = exception.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _explain = null;
        _explainError = 'Не удалось получить AI-заключение';
      });
    } finally {
      if (mounted) setState(() => _explainLoading = false);
    }
  }

  List<ResultZone> get _zones {
    final frame = widget.frame;
    if (frame != null && frame.overlays.isNotEmpty) {
      return frame.overlays.map(ResultZone.fromOverlay).toList(growable: false);
    }
    if (frame != null && frame.componentsStatus.isNotEmpty) {
      return frame.componentsStatus
          .map(ResultZone.fromStatus)
          .toList(growable: false);
    }
    return widget.equipment.components
        .map(ResultZone.fromComponent)
        .toList(growable: false);
  }

  List<DiagnosticEvent> get _diagnostics {
    final frame = widget.frame;
    if (frame != null && frame.activeDiagnostics.isNotEmpty) {
      return frame.activeDiagnostics;
    }
    return widget.telemetry?.activeDiagnostics ?? const <DiagnosticEvent>[];
  }

  List<BlockedDiagnostic> get _blocked {
    final frame = widget.frame;
    if (frame != null && frame.blockedDiagnostics.isNotEmpty) {
      return frame.blockedDiagnostics;
    }
    return widget.telemetry?.blockedDiagnostics ?? const <BlockedDiagnostic>[];
  }

  List<NormalDiagnostic> get _normal {
    final frame = widget.frame;
    if (frame != null && frame.normalDiagnostics.isNotEmpty) {
      return frame.normalDiagnostics;
    }
    return const <NormalDiagnostic>[];
  }

  Future<void> _save() async {
    if (_saving) return;
    setState(() => _saving = true);
    final api = AppScope.of(context).api;
    final frame = widget.frame;
    try {
      final saved = await api.saveInspection(
        equipmentId: widget.equipment.id,
        detections: frame?.detections ?? const <FrameDetection>[],
        frameId: frame?.frameId,
        imageWidth: frame?.imageWidth,
        imageHeight: frame?.imageHeight,
        inferenceMs: frame?.inferenceMs,
        events: widget.events,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => AlertDialog(
          icon: const Icon(
            Icons.check_circle_outline,
            color: AppColors.green,
            size: 40,
          ),
          title: Text('Осмотр №${saved.id} сохранён'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Состояние узлов на сервере обновлено:',
                style: TextStyle(color: AppColors.textMuted, fontSize: 13),
              ),
              const SizedBox(height: 8),
              Text(
                saved.updatedComponentsText,
                style: const TextStyle(fontSize: 13.5, height: 1.35),
              ),
              const SizedBox(height: 10),
              Text(
                saved.createdAt == null
                    ? ''
                    : DateFormat('dd.MM.yyyy HH:mm')
                          .format(DateTime.parse(saved.createdAt!).toLocal()),
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                ),
              ),
            ],
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('К карточке'),
            ),
          ],
        ),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } on ApiException catch (exception) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Не удалось сохранить: ${exception.message}'),
          backgroundColor: AppColors.errorSurface,
        ),
      );
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Не удалось сохранить осмотр')),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final frame = widget.frame;
    final ocr = frame?.ocr;
    final analytics = frame?.analytics;
    final roster = widget.equipment.components
        .map((component) => component.name)
        .toList(growable: false);
    final verdict = frame == null
        ? InspectionVerdict.resolve(
            roster: roster,
            observations: const <VerdictObservation>[],
            hasFrame: false,
          )
        : frame.resolveVerdict(roster);
    final signComponents = analytics == null
        ? const <AnalyticsComponent>[]
        : analytics
              .withRoster(roster)
              .where((component) => component.signs.any((sign) => sign.isShown))
              .toList(growable: false);
    final notVisibleSigns = analytics == null
        ? 0
        : analytics.components
              .expand((component) => component.signs)
              .where((sign) => sign.isNotVisible)
              .length;
    final zones = _zones;
    final defects = frame?.defects ?? const <FrameDetection>[];
    final diagnostics = _diagnostics;
    final normal = _normal;
    final recommendations = <String>{
      for (final event in diagnostics)
        if (event.recommendation != null && event.recommendation!.isNotEmpty)
          event.recommendation!,
    }.toList(growable: false);

    // Вкладка «Итог»: кто, что решено, заключение модели и что делать.
    final summaryTab = ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                widget.equipment.name,
                style: const TextStyle(
                  fontSize: 21,
                  fontWeight: FontWeight.w800,
                  height: 1.2,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                '${widget.equipment.id} · ${DateFormat('dd.MM.yyyy HH:mm').format(DateTime.now())}',
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12.5,
                ),
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  StatusChip(
                    label: frame == null
                        ? 'без кадров'
                        : (frame.frameId > 0
                              ? 'кадр ${frame.frameId}'
                              : 'загруженное фото'),
                    color: frame == null ? AppColors.grey : AppColors.accent,
                    dense: true,
                  ),
                  if (frame != null)
                    StatusChip(
                      label: frame.board.detected
                          ? 'планшет: ${frame.board.markers} маркера'
                          : 'планшет не найден',
                      color: frame.board.detected
                          ? AppColors.green
                          : AppColors.yellow,
                      dense: true,
                    ),
                  if (frame != null)
                    StatusChip(
                      label:
                          'Видно ${frame.visibleOverlayCount} из ${frame.totalOverlayCount} узлов',
                      color: frame.visibleOverlayCount == 0
                          ? AppColors.yellow
                          : AppColors.accent,
                      dense: true,
                    ),
                  if (frame != null)
                    StatusChip(
                      label: '${frame.inferenceMs} мс',
                      color: AppColors.grey,
                      dense: true,
                    ),
                  if (ocr != null)
                    StatusChip(
                      label: ocr.value == null
                          ? 'OCR: нет показания'
                          : 'OCR: ${ocr.displayValue} ${ocr.unit}',
                      color: ocr.value == null
                          ? AppColors.grey
                          : AppColors.primary,
                      dense: true,
                    ),
                ],
              ),
            ],
          ),
        ),
        if (frame == null) ...[
          const SizedBox(height: 12),
          Panel(
            color: AppColors.yellow.withValues(alpha: 0.08),
            borderColor: AppColors.yellow.withValues(alpha: 0.5),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, color: AppColors.yellow, size: 18),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Кадры от сервера не получены. Осмотр будет сохранён '
                    'с текущей диагностикой сервера и без детекций.',
                    style: TextStyle(fontSize: 12.5, height: 1.35),
                  ),
                ),
              ],
            ),
          ),
        ],
        const SizedBox(height: 16),
        VerdictCard(verdict: verdict),
        const SizedBox(height: 18),
        Row(
          children: [
            const Icon(Icons.auto_awesome, size: 16, color: AppColors.accent),
            const SizedBox(width: 7),
            const Expanded(
              child: Text(
                'AI-ЗАКЛЮЧЕНИЕ',
                style: TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1,
                ),
              ),
            ),
            if (_explainLoading)
              const Padding(
                padding: EdgeInsets.only(right: 8),
                child: SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            else
              IconButton(
                tooltip: 'Обновить AI-заключение',
                visualDensity: VisualDensity.compact,
                onPressed: _loadExplain,
                icon: const Icon(Icons.refresh, size: 18),
              ),
          ],
        ),
        const SizedBox(height: 4),
        if (_explainLoading)
          const Panel(
            child: Row(
              children: [
                SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Модель готовит заключение…',
                    style: TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 12.5,
                    ),
                  ),
                ),
              ],
            ),
          )
        else if (_explainError != null)
          Panel(
            color: AppColors.orange.withValues(alpha: 0.07),
            borderColor: AppColors.orange.withValues(alpha: 0.5),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(
                      Icons.cloud_off,
                      color: AppColors.orange,
                      size: 18,
                    ),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text(
                        'AI-заключение недоступно',
                        style: TextStyle(
                          color: AppColors.orange,
                          fontSize: 14,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    TextButton(
                      onPressed: _loadExplain,
                      child: const Text('Объяснить'),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  _explainError!,
                  style: const TextStyle(
                    color: AppColors.textMuted,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              ],
            ),
          )
        else if (_explain != null)
          VisionExplainCard(explain: _explain!),
        const SizedBox(height: 18),
        const _SectionTitle('РЕКОМЕНДАЦИИ'),
        const SizedBox(height: 8),
        Panel(
          child: recommendations.isEmpty
              ? const Text(
                  'Специальных рекомендаций нет',
                  style: TextStyle(color: AppColors.textMuted),
                )
              : Column(
                  children: [
                    for (final recommendation in recommendations)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.build_circle_outlined,
                              color: AppColors.accent,
                              size: 18,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                recommendation,
                                style: const TextStyle(
                                  fontSize: 13.5,
                                  height: 1.35,
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
    );

    // Вкладка «Дефекты»: что нашли, где и насколько уверенно.
    final defectsTab = ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        const _SectionTitle('ЧТО ОБНАРУЖЕНО'),
        const SizedBox(height: 8),
        Panel(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(
            children: [
              for (var index = 0; index < zones.length; index++) ...[
                _ZoneRow(zone: zones[index]),
                if (index < zones.length - 1)
                  const Divider(color: AppColors.outline, height: 1),
              ],
              if (zones.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text(
                    'Нет данных о состоянии узлов',
                    style: TextStyle(color: AppColors.textMuted),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        if (defects.isNotEmpty)
          Panel(
            color: AppColors.red.withValues(alpha: 0.07),
            borderColor: AppColors.red.withValues(alpha: 0.5),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'ОБНАРУЖЕННЫЕ ДЕФЕКТЫ',
                  style: TextStyle(
                    color: AppColors.red,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.8,
                  ),
                ),
                const SizedBox(height: 6),
                for (final defect in defects)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Row(
                      children: [
                        const Icon(
                          Icons.warning_amber_rounded,
                          color: AppColors.red,
                          size: 16,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            '${componentLabel(defect.className)} · '
                            'достоверность ${(defect.confidence * 100).round()}%',
                            style: const TextStyle(fontSize: 13),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        if (signComponents.isNotEmpty || notVisibleSigns >= 2) ...[
          const SizedBox(height: 18),
          _SectionTitle(
            'ВИЗУАЛЬНЫЕ ПРИЗНАКИ',
            trailing: notVisibleSigns > 0
                ? 'не проверено: $notVisibleSigns'
                : null,
          ),
          const SizedBox(height: 8),
          if (signComponents.isEmpty)
            Panel(
              child: Text(
                'Не проверено: $notVisibleSigns признаков (не видно)',
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontStyle: FontStyle.italic,
                ),
              ),
            )
          else
            for (final component in signComponents)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Panel(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(
                            Icons.visibility_outlined,
                            size: 16,
                            color: AppColors.accent,
                          ),
                          const SizedBox(width: 7),
                          Expanded(
                            child: Text(
                              component.label,
                              style: const TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                          AnalyticsStatusBadge(
                            component: component,
                            short: true,
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      VisualSignsTable(signs: component.signs, compact: true),
                    ],
                  ),
                ),
              ),
        ],
        if (frame?.vision != null) ...[
          const SizedBox(height: 16),
          const _SectionTitle('AI-ПРОВЕРКА КАДРА'),
          const SizedBox(height: 8),
          VisionCheckCard(check: frame!.vision!),
        ],
        if (analytics != null && !analytics.isEmpty) ...[
          const SizedBox(height: 18),
          _SectionTitle('ВИДЕОАНАЛИТИКА', trailing: analytics.headerText),
          const SizedBox(height: 8),
          Panel(
            child: AnalyticsTable(
              analytics: analytics,
              roster: widget.equipment.components
                  .map((component) => component.name)
                  .toList(growable: false),
            ),
          ),
        ],
      ],
    );

    // Вкладка «Диагностика»: правила, evidence и чего не хватило.
    final diagnosticsTab = ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _SectionTitle(
          'АКТИВНЫЕ ДИАГНОСТИКИ',
          trailing: diagnostics.isEmpty ? null : '${diagnostics.length}',
        ),
        const SizedBox(height: 8),
        if (diagnostics.isEmpty)
          const Panel(
            child: Text(
              'Активных диагностик нет',
              style: TextStyle(color: AppColors.textMuted),
            ),
          )
        else
          Column(
            children: [
              for (final event in diagnostics)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: DiagnosticCard(event: event, showEvidence: true),
                ),
            ],
          ),
        if (_blocked.isNotEmpty) ...[
          const SizedBox(height: 18),
          _SectionTitle(
            'ЧЕГО НЕ ХВАТИЛО',
            trailing: '${_blocked.length} правил',
          ),
          const SizedBox(height: 8),
          Panel(
            child: Column(
              children: [
                for (final item in _blocked)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(
                          Icons.block,
                          size: 15,
                          color: AppColors.textMuted,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            '${item.title}: ${blockedReasonLabel(item.blockedBy, item.reason)}',
                            style: const TextStyle(
                              color: AppColors.textMuted,
                              fontSize: 12.5,
                              height: 1.3,
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
        if (normal.isNotEmpty) ...[
          const SizedBox(height: 18),
          _SectionTitle(
            'ПРОВЕРЕНО · В ПРЕДЕЛАХ НОРМЫ',
            trailing: '${normal.length} правил',
          ),
          const SizedBox(height: 8),
          Panel(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (var index = 0; index < normal.length; index++) ...[
                  _NormalRuleRow(item: normal[index]),
                  if (index < normal.length - 1)
                    const Divider(color: AppColors.outline, height: 1),
                ],
              ],
            ),
          ),
        ],
        if (frame != null) ...[
          const SizedBox(height: 18),
          const _SectionTitle('НАБЛЮДЕНИЕ'),
          const SizedBox(height: 8),
          Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _FactRow(
                  label: 'Планшет',
                  value: frame.board.detected
                      ? 'найден · ${frame.board.markers} маркера · '
                            '${frame.board.confidence.toStringAsFixed(3)}'
                      : boardReasonLabel(frame.board.reason),
                  ok: frame.board.detected,
                ),
                if (frame.board.reprojectionError != null)
                  _FactRow(
                    label: 'Ошибка репроекции',
                    value:
                        '${frame.board.reprojectionError!.toStringAsFixed(2)} px',
                  ),
                if (frame.hasFrameSize)
                  _FactRow(
                    label: 'Кадр',
                    value: '${frame.imageWidth}×${frame.imageHeight} px',
                  ),
                if (ocr != null)
                  _FactRow(
                    label: 'Манометр (OCR)',
                    value: ocr.value == null
                        ? ocrErrorLabel(ocr.error)
                        : '${ocr.displayValue} ${ocr.unit} · '
                              '${(ocr.confidence * 100).round()}%',
                  ),
                _FactRow(
                  label: 'Время обработки',
                  value: '${frame.inferenceMs} мс',
                ),
              ],
            ),
          ),
        ],
      ],
    );

    // Вкладка «Журнал»: что происходило во время осмотра.
    final journalTab = ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _SectionTitle('ЖУРНАЛ ОСМОТРА', trailing: '${widget.events.length}'),
        const SizedBox(height: 8),
        Panel(
          padding: EdgeInsets.zero,
          child: Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              initiallyExpanded: true,
              tilePadding: const EdgeInsets.symmetric(
                horizontal: 14,
                vertical: 2,
              ),
              childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
              iconColor: AppColors.textMuted,
              collapsedIconColor: AppColors.textMuted,
              title: Row(
                children: [
                  const Icon(
                    Icons.receipt_long_outlined,
                    size: 17,
                    color: AppColors.accent,
                  ),
                  const SizedBox(width: 8),
                  const Expanded(
                    child: Text(
                      'Журнал осмотра',
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  Text(
                    '${widget.events.length} событий',
                    style: const TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
              children: [
                if (widget.events.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text(
                      'Событий за осмотр не было',
                      style: TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  )
                else
                  for (final event in widget.events)
                    InspectionEventTile(event: event, dense: true),
              ],
            ),
          ),
        ),
      ],
    );

    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text('Результат · ${widget.equipment.id}'),
          actions: [
            if (widget.frozenJpeg != null)
              Padding(
                padding: const EdgeInsets.only(right: 12),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: ColoredBox(
                    color: Colors.black,
                    child: Image.memory(
                      widget.frozenJpeg!,
                      width: 62,
                      height: 40,
                      fit: BoxFit.contain,
                    ),
                  ),
                ),
              ),
          ],
          bottom: const TabBar(
            tabs: [
              Tab(text: 'Итог'),
              Tab(text: 'Дефекты'),
              Tab(text: 'Диагностика'),
              Tab(text: 'Журнал'),
            ],
          ),
        ),
        body: TabBarView(
          children: [summaryTab, defectsTab, diagnosticsTab, journalTab],
        ),
        bottomNavigationBar: Container(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
          decoration: BoxDecoration(
            color: palette.surface,
            border: Border(top: BorderSide(color: palette.outline)),
          ),
          child: SafeArea(
            top: false,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                FilledButton.icon(
                  key: const Key('save-inspection-button'),
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save_outlined),
                  label: Text(_saving ? 'Сохраняем…' : 'Сохранить осмотр'),
                ),
                const SizedBox(height: 6),
                Text(
                  frame == null
                      ? 'Будет сохранено без детекций — только диагностика сервера'
                      : 'Детекций к сохранению: ${frame.detections.length} · '
                            'диагностик: ${diagnostics.length}',
                  style: const TextStyle(
                    color: AppColors.textMuted,
                    fontSize: 11.5,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _NormalRuleRow extends StatelessWidget {
  const _NormalRuleRow({required this.item});

  final NormalDiagnostic item;

  @override
  Widget build(BuildContext context) {
    final confidence = (item.confidence * 100).round();
    final threshold = item.activateConfidence == null
        ? null
        : (item.activateConfidence! * 100).round();
    final parts = <String>[
      'уверенность $confidence%',
      if (threshold != null) 'порог $threshold%',
      if (item.conditionsTotal > 0)
        '${item.conditionsMatched} из ${item.conditionsTotal} условий',
    ];
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.check_circle_outline,
            color: AppColors.green,
            size: 17,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.title,
                  style: const TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  parts.join(' · '),
                  style: const TextStyle(
                    color: AppColors.textMuted,
                    fontSize: 11.5,
                    height: 1.3,
                  ),
                ),
                if (item.suppressedBy != null) ...[
                  const SizedBox(height: 2),
                  Text(
                    'подавлено более сильным правилом: ${item.suppressedBy}',
                    style: const TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 11,
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ZoneRow extends StatelessWidget {
  const _ZoneRow({required this.zone});

  final ResultZone zone;

  @override
  Widget build(BuildContext context) {
    final color = zone.visible ? AppColors.byName(zone.color) : AppColors.grey;
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
      child: Row(
        children: [
          Container(
            width: 4,
            height: 34,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  zone.label,
                  style: const TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (!zone.visible)
                  const Padding(
                    padding: EdgeInsets.only(top: 2),
                    child: Text(
                      'не в кадре · значения не подставляются',
                      style: TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11.5,
                      ),
                    ),
                  )
                else if (zone.defects.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      zone.defects
                          .map((item) => componentLabel(item))
                          .join(', '),
                      style: const TextStyle(
                        color: AppColors.red,
                        fontSize: 11.5,
                      ),
                    ),
                  )
                else if (zone.detail != null)
                  Text(
                    'Остаток: ${zone.detail}',
                    style: const TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 11.5,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (zone.visible)
            StatusChip.status(
              status: zone.status,
              label: componentStatusLabel(zone.status),
              dense: true,
            )
          else
            const StatusChip(
              label: 'не в кадре',
              color: AppColors.grey,
              dense: true,
            ),
        ],
      ),
    );
  }
}

class _FactRow extends StatelessWidget {
  const _FactRow({required this.label, required this.value, this.ok});

  final String label;
  final String value;
  final bool? ok;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 138,
            child: Text(
              label,
              style: const TextStyle(
                color: AppColors.textMuted,
                fontSize: 12.5,
              ),
            ),
          ),
          Expanded(
            child: Row(
              children: [
                if (ok != null) ...[
                  Icon(
                    ok! ? Icons.check_circle_outline : Icons.error_outline,
                    size: 14,
                    color: ok! ? AppColors.green : AppColors.yellow,
                  ),
                  const SizedBox(width: 5),
                ],
                Expanded(
                  child: Text(value, style: const TextStyle(fontSize: 12.5)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text, {this.trailing});

  final String text;
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Row(
      children: [
        Text(
          text,
          style: TextStyle(
            color: palette.text,
            fontSize: 14,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.3,
          ),
        ),
        const Spacer(),
        if (trailing != null)
          Text(
            trailing!,
            style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
          ),
      ],
    );
  }
}
