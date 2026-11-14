import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/labels.dart';
import '../models/telemetry.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_indicator.dart';
import '../widgets/diagnostic_card.dart';
import '../widgets/metric_tile.dart';
import '../widgets/status_chip.dart';
import '../widgets/telemetry_chart.dart';
import 'ar_inspection_screen.dart';
import 'component_detail_screen.dart';
import 'history_screen.dart';
import 'settings_screen.dart';

class _EquipmentData {
  const _EquipmentData({
    required this.equipment,
    required this.diagnostics,
    required this.telemetry,
  });

  final Equipment equipment;
  final DiagnosticsSnapshot diagnostics;
  final TelemetryHistory telemetry;
}

/// Карточка агрегата: узлы, диагностики, телеметрия за 5 минут.
class EquipmentScreen extends StatefulWidget {
  const EquipmentScreen({super.key, required this.equipmentId});

  final String equipmentId;

  @override
  State<EquipmentScreen> createState() => _EquipmentScreenState();
}

class _EquipmentScreenState extends State<EquipmentScreen> {
  late Future<_EquipmentData> _future;
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      _future = _load();
    }
  }

  Future<_EquipmentData> _load() async {
    final api = AppScope.of(context).api;
    final equipment = await api.fetchEquipment(widget.equipmentId);
    final diagnostics = await api.fetchDiagnostics(widget.equipmentId);
    final telemetry = await api.fetchTelemetry(widget.equipmentId, minutes: 5);
    return _EquipmentData(
      equipment: equipment,
      diagnostics: diagnostics,
      telemetry: telemetry,
    );
  }

  void _reload() => setState(() => _future = _load());

  Future<void> _startInspection(Equipment equipment) async {
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ArInspectionScreen(equipment: equipment),
      ),
    );
    if (saved == true) _reload();
  }

  void _openHistory(Equipment equipment) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => HistoryScreen(
          equipmentId: equipment.id,
          equipmentName: equipment.name,
          roster: equipment.components
              .map((component) => component.name)
              .toList(growable: false),
        ),
      ),
    );
  }

  void _openComponent(
    Equipment equipment,
    ComponentInfo component,
    List<DiagnosticEvent> diagnostics,
  ) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ComponentDetailScreen(
          equipment: equipment,
          component: component,
          diagnostics: diagnostics,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.equipmentId),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 6),
            child: Center(child: ConnectionIndicator(showHost: false)),
          ),
          IconButton(
            tooltip: 'Обновить',
            onPressed: _reload,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: 'Подключение',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const SettingsScreen()),
            ),
            icon: const Icon(Icons.settings_outlined),
          ),
        ],
      ),
      body: FutureBuilder<_EquipmentData>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError || !snapshot.hasData) {
            final error = snapshot.error;
            return _ErrorView(
              message: error is ApiException
                  ? error.message
                  : 'Не удалось загрузить карточку агрегата',
              onRetry: _reload,
              onSettings: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const SettingsScreen()),
              ),
            );
          }
          final data = snapshot.data!;
          return _buildContent(data);
        },
      ),
      bottomNavigationBar: FutureBuilder<_EquipmentData>(
        future: _future,
        builder: (context, snapshot) {
          final equipment = snapshot.data?.equipment;
          return Container(
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
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      key: const Key('start-inspection-button'),
                      onPressed: equipment == null
                          ? null
                          : () => _startInspection(equipment),
                      icon: const Icon(Icons.view_in_ar_outlined),
                      label: const Text('Начать осмотр'),
                    ),
                  ),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: equipment == null
                          ? null
                          : () => _openHistory(equipment),
                      icon: const Icon(Icons.history),
                      label: const Text('История осмотров'),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _buildContent(_EquipmentData data) {
    final equipment = data.equipment;
    final telemetry = data.telemetry;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _EquipmentHeader(equipment: equipment),
        const SizedBox(height: 18),
        const _SectionTitle('СОСТОЯНИЕ УЗЛОВ'),
        const SizedBox(height: 8),
        if (equipment.components.isEmpty)
          const Panel(
            child: Text(
              'Состав узлов не задан',
              style: TextStyle(color: AppColors.textMuted),
            ),
          )
        else
          Panel(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                for (
                  var index = 0;
                  index < equipment.components.length;
                  index++
                ) ...[
                  _ComponentRow(
                    component: equipment.components[index],
                    onTap: () => _openComponent(
                      equipment,
                      equipment.components[index],
                      data.diagnostics.active,
                    ),
                  ),
                  if (index < equipment.components.length - 1)
                    const Divider(color: AppColors.outline, height: 1),
                ],
              ],
            ),
          ),
        const SizedBox(height: 18),
        _SectionTitle(
          'АКТИВНЫЕ ДИАГНОСТИКИ',
          trailing: data.diagnostics.scenario == null
              ? null
              : 'сценарий: ${scenarioLabel(data.diagnostics.scenario)}',
        ),
        const SizedBox(height: 8),
        if (data.diagnostics.active.isEmpty)
          Panel(
            borderColor: AppColors.green.withValues(alpha: 0.4),
            color: AppColors.green.withValues(alpha: 0.06),
            child: const Row(
              children: [
                Icon(Icons.verified_outlined, color: AppColors.green),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Активных диагностик нет',
                    style: TextStyle(
                      color: AppColors.green,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
          )
        else
          Column(
            children: [
              for (final event in data.diagnostics.active)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: DiagnosticCard(event: event, compact: true),
                ),
            ],
          ),
        const SizedBox(height: 18),
        const _SectionTitle('ТЕЛЕМЕТРИЯ · 5 МИН'),
        const SizedBox(height: 8),
        Panel(
          child: Column(
            children: [
              TelemetryChart(
                title: signalLabel('vibration_rms'),
                unit: signalUnit('vibration_rms'),
                color: AppColors.accent,
                values: telemetry.series('vibration_rms'),
                latest: telemetry.latestSignal('vibration_rms') == null
                    ? null
                    : formatSignal(
                        'vibration_rms',
                        telemetry.latestSignal('vibration_rms'),
                      ),
              ),
              const SizedBox(height: 14),
              TelemetryChart(
                title: signalLabel('temperature_bearing'),
                unit: signalUnit('temperature_bearing'),
                color: AppColors.orange,
                values: telemetry.series('temperature_bearing'),
                latest: telemetry.latestSignal('temperature_bearing') == null
                    ? null
                    : formatSignal(
                        'temperature_bearing',
                        telemetry.latestSignal('temperature_bearing'),
                      ),
              ),
              const SizedBox(height: 14),
              TelemetryChart(
                title: signalLabel('pressure_outlet'),
                unit: signalUnit('pressure_outlet'),
                color: AppColors.green,
                values: telemetry.series('pressure_outlet'),
                latest: telemetry.latestSignal('pressure_outlet') == null
                    ? null
                    : formatSignal(
                        'pressure_outlet',
                        telemetry.latestSignal('pressure_outlet'),
                      ),
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  const Icon(
                    Icons.schedule,
                    size: 14,
                    color: AppColors.textMuted,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    telemetry.samples.isEmpty
                        ? 'Нет сэмплов за 5 минут'
                        : '${telemetry.samples.length} сэмплов · ${telemetry.minutes} мин',
                    style: const TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 11.5,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _EquipmentHeader extends StatelessWidget {
  const _EquipmentHeader({required this.equipment});

  final Equipment equipment;

  @override
  Widget build(BuildContext context) {
    final worst = equipment.worstCondition;
    final minResource = equipment.components
        .map((component) => component.remainingResource?.valueYears)
        .whereType<double>()
        .fold<double?>(
          null,
          (min, value) => min == null || value < min ? value : min,
        );
    return Panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  equipment.name,
                  style: const TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w800,
                    height: 1.15,
                    letterSpacing: -0.4,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              StatusChip.status(
                status: worst,
                label: componentStatusLabel(worst),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            [
              if (equipment.model != null) 'Модель ${equipment.model}',
              if (equipment.serial != null) 'Серийный ${equipment.serial}',
              if (equipment.location != null) equipment.location!,
            ].join(' · '),
            style: const TextStyle(
              color: AppColors.textMuted,
              fontSize: 13,
              height: 1.35,
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: MetricTile(
                  label: 'Наработка',
                  value: equipment.runtimeHours == null
                      ? '—'
                      : equipment.runtimeHours!.toStringAsFixed(0),
                  unit: 'м/ч',
                  color: AppColors.primary,
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: MetricTile(
                  label: 'Нормативный срок',
                  value: equipment.normativeLifeYears == null
                      ? '—'
                      : equipment.normativeLifeYears!.toStringAsFixed(0),
                  unit: 'лет',
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: MetricTile(
                  label: 'Год выпуска',
                  value: equipment.year?.toString() ?? '—',
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: MetricTile(
                  label: 'Остаток ресурса',
                  value: minResource == null
                      ? '—'
                      : '≈ ${minResource.toStringAsFixed(1)}',
                  unit: 'лет',
                  color: minResource != null && minResource <= 0
                      ? AppColors.red
                      : null,
                  caption: 'оценка, минимум по узлам',
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              const Icon(
                Icons.build_outlined,
                size: 15,
                color: AppColors.textMuted,
              ),
              const SizedBox(width: 6),
              Text(
                'Последнее ТО: ${equipment.lastServiceDate ?? 'нет данных'}',
                style: const TextStyle(
                  color: AppColors.textMuted,
                  fontSize: 12.5,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ComponentRow extends StatelessWidget {
  const _ComponentRow({required this.component, required this.onTap});

  final ComponentInfo component;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.byName(component.condition);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
          child: Row(
            children: [
              Container(
                width: 4,
                height: 38,
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
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      'Ресурс: ${component.remainingResource?.text ?? 'н/д'}'
                      '${component.installedDate == null ? '' : ' · с ${component.installedDate}'}',
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11.5,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              StatusChip.status(
                status: component.condition,
                label: componentStatusLabel(component.condition),
                dense: true,
              ),
              const Icon(
                Icons.chevron_right,
                size: 18,
                color: AppColors.textMuted,
              ),
            ],
          ),
        ),
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

class _ErrorView extends StatelessWidget {
  const _ErrorView({
    required this.message,
    required this.onRetry,
    required this.onSettings,
  });

  final String message;
  final VoidCallback onRetry;
  final VoidCallback onSettings;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 46, color: AppColors.red),
            const SizedBox(height: 12),
            const Text(
              'Нет данных с сервера',
              style: TextStyle(fontSize: 19, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.textMuted, fontSize: 14),
            ),
            const SizedBox(height: 18),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Повторить'),
                ),
                const SizedBox(width: 12),
                OutlinedButton.icon(
                  onPressed: onSettings,
                  icon: const Icon(Icons.settings_outlined),
                  label: const Text('Настройки'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
