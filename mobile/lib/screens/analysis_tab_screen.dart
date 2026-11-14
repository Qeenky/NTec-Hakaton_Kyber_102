import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/analytics.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/labels.dart';
import '../models/telemetry.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/analytics_panel.dart';
import '../widgets/connection_indicator.dart';
import '../widgets/diagnostic_card.dart';
import '../widgets/metric_tile.dart';
import '../widgets/status_chip.dart';
import '../widgets/telemetry_chart.dart';
import 'ar_inspection_screen.dart';
import 'component_detail_screen.dart';
import 'settings_screen.dart';

class _AnalysisBundle {
  const _AnalysisBundle({
    required this.equipment,
    required this.diagnostics,
    required this.telemetry,
    this.analytics,
  });

  final Equipment equipment;
  final DiagnosticsSnapshot diagnostics;
  final TelemetryHistory telemetry;
  final FrameAnalytics? analytics;
}

/// Вкладка «Анализ»: графики вибрации/температуры, активные диагностики с evidence и оценка ресурса.
class AnalysisTabScreen extends StatefulWidget {
  const AnalysisTabScreen({super.key});

  @override
  State<AnalysisTabScreen> createState() => _AnalysisTabScreenState();
}

class _AnalysisTabScreenState extends State<AnalysisTabScreen> {
  static const List<String> _equipmentIds = <String>['PUMP-001', 'PUMP-002'];

  bool _started = false;
  late String _selectedId;
  Future<_AnalysisBundle>? _future;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final app = AppScope.of(context);
    if (!_started) {
      _started = true;
      _selectedId = app.selectedEquipmentId;
      _future = _load(_selectedId);
    } else if (app.selectedEquipmentId != _selectedId) {
      _selectedId = app.selectedEquipmentId;
      _future = _load(_selectedId);
    }
  }

  Future<_AnalysisBundle> _load(String equipmentId) async {
    final api = AppScope.of(context).api;
    final equipment = await api.fetchEquipment(equipmentId);
    final diagnostics = await api.fetchDiagnostics(equipmentId);
    final telemetry = await api.fetchTelemetry(equipmentId, minutes: 5);
    FrameAnalytics? analytics;
    try {
      analytics = await api.fetchAnalytics(equipmentId);
    } catch (_) {}
    return _AnalysisBundle(
      equipment: equipment,
      diagnostics: diagnostics,
      telemetry: telemetry,
      analytics: analytics,
    );
  }

  void _selectEquipment(String id) {
    if (id == _selectedId) return;
    AppScope.of(context).selectEquipment(id);
    setState(() {
      _selectedId = id;
      _future = _load(id);
    });
  }

  void _reload() {
    setState(() {
      _future = _load(_selectedId);
    });
  }

  Future<void> _openAr(Equipment equipment) async {
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ArInspectionScreen(equipment: equipment),
      ),
    );
    if (saved == true && mounted) _reload();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Анализ и телеметрия'),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 4),
            child: Center(child: ConnectionIndicator()),
          ),
          const ThemeModeButton(),
          IconButton(
            tooltip: 'Обновить данные',
            onPressed: _reload,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: 'Настройки подключения',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const SettingsScreen()),
            ),
            icon: const Icon(Icons.settings_outlined),
          ),
        ],
      ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildEquipmentSelector(palette),
          Divider(color: palette.outline, height: 1),
          Expanded(
            child: FutureBuilder<_AnalysisBundle>(
              future: _future,
              builder: (context, snapshot) {
                if (snapshot.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snapshot.hasError || !snapshot.hasData) {
                  final error = snapshot.error;
                  return _buildError(
                    palette,
                    error is ApiException
                        ? error.message
                        : 'Не удалось загрузить аналитику агрегата',
                  );
                }
                return _buildBody(palette, snapshot.data!);
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEquipmentSelector(AppPalette palette) {
    final selectedBg =
        palette.isDark ? const Color(0xFF0F2135) : const Color(0xFFDCEAFB);
    return Container(
      color: palette.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          for (var i = 0; i < _equipmentIds.length; i++) ...[
            if (i > 0) const SizedBox(width: 10),
            Expanded(
              child: Material(
                color: _selectedId == _equipmentIds[i]
                    ? selectedBg
                    : palette.surfaceHigh,
                borderRadius: BorderRadius.circular(12),
                child: InkWell(
                  key: Key('analysis-select-${_equipmentIds[i]}'),
                  borderRadius: BorderRadius.circular(12),
                  onTap: () => _selectEquipment(_equipmentIds[i]),
                  child: Container(
                    height: 44,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: _selectedId == _equipmentIds[i]
                            ? palette.primary
                            : palette.outline,
                        width: _selectedId == _equipmentIds[i] ? 1.5 : 1,
                      ),
                    ),
                    child: Text(
                      _equipmentIds[i],
                      style: TextStyle(
                        color: _selectedId == _equipmentIds[i]
                            ? palette.primary
                            : palette.text,
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildBody(AppPalette palette, _AnalysisBundle data) {
    final equipment = data.equipment;
    final telemetry = data.telemetry;
    final diagnostics = data.diagnostics;
    final minResource = equipment.components
        .map((c) => c.remainingResource?.valueYears)
        .whereType<double>()
        .fold<double?>(null, (min, v) => min == null || v < min ? v : min);
    final scenario = diagnostics.scenario ?? telemetry.scenario;

    return RefreshIndicator(
      onRefresh: () async => _reload(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
        children: [
          // 1. Карточка сводки и ресурса
          Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            equipment.name,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: palette.text,
                              fontSize: 20,
                              fontWeight: FontWeight.w700,
                              height: 1.2,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            '${equipment.id}'
                            '${scenario == null ? '' : ' · режим: ${scenarioLabel(scenario)}'}',
                            style: TextStyle(
                              color: palette.textMuted,
                              fontSize: 13,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    StatusChip.status(status: equipment.worstCondition),
                  ],
                ),
                const SizedBox(height: 14),
                Row(
                  children: [
                    Expanded(
                      child: MetricTile(
                        label: 'Наработка',
                        value: equipment.runtimeHours == null
                            ? '—'
                            : equipment.runtimeHours!.toStringAsFixed(0),
                        unit: 'м/ч',
                        color: palette.primary,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: MetricTile(
                        label: 'Остаток ресурса',
                        value: minResource == null
                            ? '—'
                            : '≈ ${minResource.toStringAsFixed(1)}',
                        unit: 'лет',
                        caption: 'оценка по узлам',
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),

          // 2. Графики вибрации и температуры
          Row(
            children: [
              Text(
                'ТЕЛЕМЕТРИЯ · 5 МИН',
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                ),
              ),
              const Spacer(),
              Text(
                telemetry.samples.isEmpty
                    ? 'нет отсчётов'
                    : '${telemetry.samples.length} отсчётов',
                style: TextStyle(color: palette.textMuted, fontSize: 12),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Panel(
            child: Column(
              children: [
                TelemetryChart(
                  title: signalLabel('vibration_rms'),
                  unit: signalUnit('vibration_rms'),
                  color: palette.accent,
                  values: telemetry.series('vibration_rms'),
                  latest: telemetry.latestSignal('vibration_rms') == null
                      ? null
                      : formatSignal(
                          'vibration_rms',
                          telemetry.latestSignal('vibration_rms'),
                        ),
                ),
                const SizedBox(height: 16),
                TelemetryChart(
                  title: signalLabel('temperature_bearing'),
                  unit: signalUnit('temperature_bearing'),
                  color: palette.orange,
                  values: telemetry.series('temperature_bearing'),
                  latest: telemetry.latestSignal('temperature_bearing') == null
                      ? null
                      : formatSignal(
                          'temperature_bearing',
                          telemetry.latestSignal('temperature_bearing'),
                        ),
                ),
                const SizedBox(height: 16),
                TelemetryChart(
                  title: signalLabel('temperature_motor'),
                  unit: signalUnit('temperature_motor'),
                  color: palette.yellow,
                  values: telemetry.series('temperature_motor'),
                  latest: telemetry.latestSignal('temperature_motor') == null
                      ? null
                      : formatSignal(
                          'temperature_motor',
                          telemetry.latestSignal('temperature_motor'),
                        ),
                ),
                const SizedBox(height: 16),
                TelemetryChart(
                  title: signalLabel('pressure_outlet'),
                  unit: signalUnit('pressure_outlet'),
                  color: palette.green,
                  values: telemetry.series('pressure_outlet'),
                  latest: telemetry.latestSignal('pressure_outlet') == null
                      ? null
                      : formatSignal(
                          'pressure_outlet',
                          telemetry.latestSignal('pressure_outlet'),
                        ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),

          // 3. Активные диагностики и evidence
          Row(
            children: [
              Text(
                'АКТИВНЫЕ ДИАГНОСТИКИ И EVIDENCE',
                style: TextStyle(
                  color: palette.textMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                ),
              ),
              const Spacer(),
              if (diagnostics.active.isNotEmpty)
                Text(
                  '${diagnostics.active.length}',
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (diagnostics.active.isEmpty)
            Panel(
              borderColor: palette.green.withValues(alpha: 0.45),
              color: palette.green.withValues(alpha: 0.06),
              child: Row(
                children: [
                  Icon(Icons.verified_outlined, color: palette.green),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Активных отклонений по правилам диагностики нет',
                      style: TextStyle(
                        color: palette.green,
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
            )
          else
            for (final event in diagnostics.active)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: DiagnosticCard(event: event, showEvidence: true),
              ),
          const SizedBox(height: 20),

          // 4. Ресурс по узлам
          Text(
            'ОЦЕНКА РЕСУРСА ПО УЗЛАМ',
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 8),
          Panel(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (var i = 0; i < equipment.components.length; i++) ...[
                  _ResourceNodeRow(
                    component: equipment.components[i],
                    normativeLife:
                        equipment.components[i].normativeLifeYears ??
                            equipment.normativeLifeYears ??
                            20,
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => ComponentDetailScreen(
                          equipment: equipment,
                          component: equipment.components[i],
                          diagnostics: diagnostics.active,
                        ),
                      ),
                    ),
                  ),
                  if (i < equipment.components.length - 1)
                    Divider(color: palette.outline, height: 1),
                ],
              ],
            ),
          ),

          if (data.analytics != null && !data.analytics!.isEmpty) ...[
            const SizedBox(height: 20),
            Row(
              children: [
                Text(
                  'ОКНО ВИДЕОАНАЛИТИКИ',
                  style: TextStyle(
                    color: palette.textMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                  ),
                ),
                const Spacer(),
                Text(
                  data.analytics!.headerText,
                  style: TextStyle(color: palette.textMuted, fontSize: 12),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Panel(
              child: AnalyticsTable(
                analytics: data.analytics!,
                roster: equipment.components
                    .map((c) => c.name)
                    .toList(growable: false),
              ),
            ),
          ],

          const SizedBox(height: 20),
          FilledButton.icon(
            key: const Key('analysis-start-ar-button'),
            onPressed: () => _openAr(equipment),
            icon: const Icon(Icons.view_in_ar_outlined),
            label: Text('Начать AR-осмотр · ${equipment.id}'),
          ),
        ],
      ),
    );
  }

  Widget _buildError(AppPalette palette, String message) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.cloud_off, size: 48, color: palette.yellow),
            const SizedBox(height: 12),
            Text(
              'Нет связи с сервером',
              style: TextStyle(
                color: palette.text,
                fontSize: 20,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              message,
              textAlign: TextAlign.center,
              style: TextStyle(color: palette.textSecondary, fontSize: 15),
            ),
            const SizedBox(height: 18),
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
}

class _ResourceNodeRow extends StatelessWidget {
  const _ResourceNodeRow({
    required this.component,
    required this.normativeLife,
    required this.onTap,
  });

  final ComponentInfo component;
  final double normativeLife;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = palette.byName(component.condition);
    final years = component.remainingResource?.valueYears;
    final ratio = years == null || normativeLife <= 0
        ? null
        : (years / normativeLife).clamp(0.0, 1.0);

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          child: Row(
            children: [
              Container(
                width: 4,
                height: 40,
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
                      componentLabel(component.name),
                      style: TextStyle(
                        color: palette.text,
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        if (ratio != null) ...[
                          SizedBox(
                            width: 72,
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(3),
                              child: LinearProgressIndicator(
                                value: ratio,
                                minHeight: 5,
                                backgroundColor: palette.outline,
                                valueColor:
                                    AlwaysStoppedAnimation<Color>(color),
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                        ],
                        Expanded(
                          child: Text(
                            component.remainingResource?.text ?? 'Ресурс: н/д',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: palette.textMuted,
                              fontSize: 12,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              StatusChip.status(status: component.condition, dense: true),
              const SizedBox(width: 4),
              Icon(Icons.chevron_right, size: 18, color: palette.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}
