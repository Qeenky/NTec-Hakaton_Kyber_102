import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/inspection.dart';
import '../models/labels.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/analytics_panel.dart';
import '../widgets/diagnostic_card.dart';
import '../widgets/status_chip.dart';
import '../widgets/verdict_card.dart';

/// История осмотров агрегата: `GET /inspections?equipment_id=...`.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({
    super.key,
    required this.equipmentId,
    required this.equipmentName,
    this.roster = const <String>[],
  });

  final String equipmentId;
  final String equipmentName;
  final List<String> roster;

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  late Future<List<InspectionRecord>> _future;
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      _future = AppScope.of(context).api.fetchInspections(widget.equipmentId);
    }
  }

  void _reload() {
    setState(() {
      _future = AppScope.of(context).api.fetchInspections(widget.equipmentId);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('История осмотров'),
            Text(
              '${widget.equipmentName} · ${widget.equipmentId}',
              style: const TextStyle(
                color: AppColors.textMuted,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Обновить',
            onPressed: _reload,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: FutureBuilder<List<InspectionRecord>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            final error = snapshot.error;
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.cloud_off, size: 44, color: AppColors.red),
                    const SizedBox(height: 10),
                    Text(
                      error is ApiException
                          ? error.message
                          : 'Не удалось загрузить историю',
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: AppColors.textMuted),
                    ),
                    const SizedBox(height: 16),
                    OutlinedButton.icon(
                      onPressed: _reload,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Повторить'),
                    ),
                  ],
                ),
              ),
            );
          }
          final records = snapshot.data ?? const <InspectionRecord>[];
          if (records.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.history_toggle_off,
                        size: 52, color: AppColors.textMuted),
                    SizedBox(height: 12),
                    Text(
                      'Осмотров пока нет',
                      style:
                          TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                    ),
                    SizedBox(height: 6),
                    Text(
                      'Проведите AR-осмотр и сохраните результат — '
                      'он появится здесь.',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
            itemCount: records.length,
            separatorBuilder: (_, _) => const SizedBox(height: 10),
            itemBuilder: (context, index) => _InspectionCard(
              record: records[index],
              index: records.length - index,
              roster: widget.roster,
            ),
          );
        },
      ),
    );
  }
}

class _InspectionCard extends StatelessWidget {
  const _InspectionCard({
    required this.record,
    required this.index,
    required this.roster,
  });

  final InspectionRecord record;
  final int index;
  final List<String> roster;

  @override
  Widget build(BuildContext context) {
    final summary = record.summary;
    final date = record.parsedAt;
    final dateText = date == null
        ? (record.createdAt ?? '—')
        : DateFormat('dd.MM.yyyy HH:mm').format(date.toLocal());
    final verdict = record.effectiveVerdict(roster: roster);

    return Panel(
      padding: EdgeInsets.zero,
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
          iconColor: AppColors.textMuted,
          collapsedIconColor: AppColors.textMuted,
          title: Row(
            children: [
              Text(
                '#${record.id}',
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(width: 10),
              Text(
                dateText,
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12.5,
                ),
              ),
              const Spacer(),
              VerdictBadge(verdict: verdict),
            ],
          ),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              [
                if (summary.frameId != null) 'кадр ${summary.frameId}',
                if (summary.inferenceMs != null)
                  'обработка ${summary.inferenceMs} мс',
                if (summary.detections.isNotEmpty)
                  'детекций ${summary.detections.length}',
                if (record.diagnostics.isNotEmpty)
                  'диагностик ${record.diagnostics.length}',
              ].join(' · '),
              style: const TextStyle(
                color: AppColors.textMuted,
                fontSize: 12,
              ),
            ),
          ),
          children: [
            const Divider(color: AppColors.outline),
            const SizedBox(height: 8),
            VerdictCard(verdict: verdict, compact: true),
            const SizedBox(height: 12),
            if (summary.componentsStatus.isNotEmpty) ...[
              const Text(
                'СОСТОЯНИЕ УЗЛОВ',
                style: TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                ),
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final item in summary.componentsStatus)
                    StatusChip(
                      label:
                          '${componentLabel(item.componentId)} · ${componentStatusLabel(item.status)}',
                      color: AppColors.byName(item.status),
                      dense: true,
                    ),
                ],
              ),
              const SizedBox(height: 12),
            ],
            if (record.analytics != null && !record.analytics!.isEmpty) ...[
              const Text(
                'ВИДЕОАНАЛИТИКА',
                style: TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                record.analytics!.headerText,
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 11,
                ),
              ),
              const SizedBox(height: 6),
              AnalyticsTable(
                analytics: record.analytics!,
                roster: const <String>[],
              ),
              const SizedBox(height: 12),
            ],
            if (summary.detections.isNotEmpty) ...[
              const Text(
                'ДЕТЕКЦИИ',
                style: TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                ),
              ),
              const SizedBox(height: 4),
              for (final detection in summary.detections)
                Padding(
                  padding: const EdgeInsets.only(top: 3),
                  child: Row(
                    children: [
                      Icon(
                        detection.isDefect
                            ? Icons.warning_amber_rounded
                            : Icons.check_circle_outline,
                        size: 14,
                        color: detection.isDefect
                            ? AppColors.red
                            : AppColors.green,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '${componentLabel(detection.className)} · '
                          '${(detection.confidence * 100).round()}%',
                          style: const TextStyle(fontSize: 12.5),
                        ),
                      ),
                    ],
                  ),
                ),
              const SizedBox(height: 12),
            ],
            if (record.diagnostics.isNotEmpty) ...[
              const Text(
                'ДИАГНОСТИКА НА МОМЕНТ ОСМОТРА',
                style: TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                ),
              ),
              const SizedBox(height: 6),
              for (final event in record.diagnostics)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: DiagnosticCard(
                    event: event,
                    compact: true,
                    showEvidence: true,
                  ),
                ),
            ] else
              const Text(
                'Активных диагностик на момент осмотра не было',
                style: TextStyle(color: AppColors.textMuted, fontSize: 12.5),
              ),
          ],
        ),
      ),
    );
  }
}
