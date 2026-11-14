import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/labels.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_indicator.dart';
import '../widgets/metric_tile.dart';
import '../widgets/status_chip.dart';
import 'ar_inspection_screen.dart';
import 'component_detail_screen.dart';
import 'equipment_screen.dart';
import 'settings_screen.dart';

/// Вкладка «Оборудование»: карточки PUMP-001 и PUMP-002, узлы, состояние и ресурс.
class EquipmentTabScreen extends StatefulWidget {
  const EquipmentTabScreen({super.key});

  @override
  State<EquipmentTabScreen> createState() => _EquipmentTabScreenState();
}

class _EquipmentTabScreenState extends State<EquipmentTabScreen> {
  static const List<String> _defaultIds = <String>['PUMP-001', 'PUMP-002'];

  bool _started = false;
  late Future<List<Equipment>> _future;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      _future = _loadAll();
    }
  }

  Future<List<Equipment>> _loadAll() async {
    final api = AppScope.of(context).api;
    List<Equipment> baseList = const <Equipment>[];
    try {
      baseList = await api.fetchEquipmentList();
    } catch (_) {}

    final ids = <String>{
      ..._defaultIds,
      for (final item in baseList)
        if (item.id.isNotEmpty) item.id,
    }.toList(growable: false);

    final detailed = <Equipment>[];
    Object? lastError;
    for (final id in ids) {
      try {
        final eq = await api.fetchEquipment(id);
        detailed.add(eq);
      } catch (e) {
        lastError = e;
        for (final fallback in baseList) {
          if (fallback.id == id) {
            detailed.add(fallback);
            break;
          }
        }
      }
    }
    if (detailed.isEmpty && lastError != null) {
      throw lastError;
    }
    return detailed;
  }

  void _reload() {
    setState(() {
      _future = _loadAll();
    });
  }

  Future<void> _startAr(Equipment equipment) async {
    await AppScope.of(context).selectEquipment(equipment.id);
    if (!mounted) return;
    final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ArInspectionScreen(equipment: equipment),
      ),
    );
    if (saved == true && mounted) _reload();
  }

  Future<void> _openEquipmentCard(Equipment equipment) async {
    await AppScope.of(context).selectEquipment(equipment.id);
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => EquipmentScreen(equipmentId: equipment.id),
      ),
    );
    if (mounted) _reload();
  }

  void _openNode(Equipment equipment, ComponentInfo component) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ComponentDetailScreen(
          equipment: equipment,
          component: component,
          diagnostics: const <DiagnosticEvent>[],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final selectedId = AppScope.of(context).selectedEquipmentId;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Оборудование'),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 4),
            child: Center(child: ConnectionIndicator()),
          ),
          const ThemeModeButton(),
          IconButton(
            tooltip: 'Обновить список',
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
      body: FutureBuilder<List<Equipment>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            final err = snapshot.error;
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.cloud_off, size: 48, color: palette.yellow),
                    const SizedBox(height: 12),
                    Text(
                      'Не удалось загрузить оборудование',
                      style: TextStyle(
                        color: palette.text,
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      err is ApiException ? err.message : '$err',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: palette.textSecondary,
                        fontSize: 15,
                      ),
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

          final items = snapshot.data ?? const <Equipment>[];
          if (items.isEmpty) {
            return Center(
              child: Text(
                'Список оборудования пуст',
                style: TextStyle(color: palette.textMuted, fontSize: 16),
              ),
            );
          }

          final activeEquipment = items.firstWhere(
            (e) => e.id == selectedId,
            orElse: () => items.first,
          );

          return Column(
            children: [
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => _reload(),
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                    itemCount: items.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 20),
                    itemBuilder: (context, index) {
                      final equipment = items[index];
                      final isSelected = equipment.id == activeEquipment.id;
                      return _EquipmentOverviewCard(
                        equipment: equipment,
                        selected: isSelected,
                        onSelect: () => AppScope.of(context)
                            .selectEquipment(equipment.id),
                        onOpenDetails: () => _openEquipmentCard(equipment),
                        onTapNode: (node) => _openNode(equipment, node),
                      );
                    },
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                decoration: BoxDecoration(
                  color: palette.surface,
                  border: Border(top: BorderSide(color: palette.outline)),
                ),
                child: FilledButton.icon(
                  key: const Key('equipment-tab-start-ar'),
                  onPressed: () => _startAr(activeEquipment),
                  icon: const Icon(Icons.view_in_ar_outlined),
                  label: Text('Начать осмотр · ${activeEquipment.id}'),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _EquipmentOverviewCard extends StatelessWidget {
  const _EquipmentOverviewCard({
    required this.equipment,
    required this.selected,
    required this.onSelect,
    required this.onOpenDetails,
    required this.onTapNode,
  });

  final Equipment equipment;
  final bool selected;
  final VoidCallback onSelect;
  final VoidCallback onOpenDetails;
  final ValueChanged<ComponentInfo> onTapNode;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final worst = equipment.worstCondition;
    final minResource = equipment.components
        .map((c) => c.remainingResource?.valueYears)
        .whereType<double>()
        .fold<double?>(null, (min, v) => min == null || v < min ? v : min);
    final problemNodes = equipment.components
        .where(
          (c) =>
              c.condition == 'possible_defect' ||
              c.condition == 'check_required',
        )
        .toList(growable: false);

    return Panel(
      borderColor: selected ? palette.primary : palette.outline,
      padding: const EdgeInsets.all(16),
      onTap: onSelect,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 1. Статус и идентификация
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
                        fontSize: 22,
                        fontWeight: FontWeight.w700,
                        height: 1.18,
                        letterSpacing: -0.3,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      [
                        equipment.id,
                        if (equipment.model != null) equipment.model!,
                        if (equipment.location != null) equipment.location!,
                      ].join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              StatusChip.status(status: worst),
            ],
          ),
          const SizedBox(height: 14),

          // 2. Две ключевые метрики (по дизайн-системе)
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
                  compact: true,
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
                  compact: true,
                ),
              ),
            ],
          ),

          const SizedBox(height: 14),
          Divider(color: palette.outline, height: 1),
          const SizedBox(height: 12),

          // 3. Проблема / Рекомендация (или плановое ТО при норме)
          if (problemNodes.isNotEmpty) ...[
            Text(
              'ПРОБЛЕМА',
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              'Отклонения на узлах: ${problemNodes.map((n) => componentLabel(n.name)).join(', ')}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.text,
                fontSize: 15,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '→ Провести прицельный AR-осмотр проблемных узлов и выполнить ТО',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.text,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 12),
          ] else ...[
            Row(
              children: [
                Icon(Icons.event_available_outlined,
                    size: 16, color: palette.green),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Все узлы в норме · нормативный срок ${equipment.normativeLifeYears?.toStringAsFixed(0) ?? '20'} лет',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: palette.textSecondary,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
          ],

          // 4. Узлы агрегата (node rows 64 px)
          if (equipment.components.isNotEmpty) ...[
            Container(
              decoration: BoxDecoration(
                color: palette.surfaceHigh.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: palette.outline),
              ),
              child: Column(
                children: [
                  for (var i = 0; i < equipment.components.length; i++) ...[
                    _NodeListRow(
                      component: equipment.components[i],
                      onTap: () => onTapNode(equipment.components[i]),
                    ),
                    if (i < equipment.components.length - 1)
                      Divider(color: palette.outline, height: 1),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 14),
          ],

          // 5. Действие карточки
          OutlinedButton.icon(
            key: Key('open-card-${equipment.id}'),
            onPressed: onOpenDetails,
            icon: const Icon(Icons.assignment_outlined, size: 20),
            label: const Text('Все детали и история'),
          ),
        ],
      ),
    );
  }
}

class _NodeListRow extends StatelessWidget {
  const _NodeListRow({
    required this.component,
    required this.onTap,
  });

  final ComponentInfo component;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = palette.byName(component.condition);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 64),
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
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          height: 1.25,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        'Ресурс: ${component.remainingResource?.text ?? 'н/д'}'
                        '${component.installedDate == null ? '' : ' · с ${component.installedDate}'}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.textMuted,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                StatusChip.status(
                  status: component.condition,
                  dense: true,
                ),
                const SizedBox(width: 4),
                Icon(
                  Icons.chevron_right,
                  size: 18,
                  color: palette.outlineStrong,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
