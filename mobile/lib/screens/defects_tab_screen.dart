import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/inspection.dart';
import '../models/labels.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_indicator.dart';
import '../widgets/status_chip.dart';
import 'component_detail_screen.dart';
import 'equipment_screen.dart';
import 'settings_screen.dart';

/// Человекочитаемое название типа дефекта или визуального признака.
String defectTypeLabel(String code) {
  switch (code) {
    case 'corrosion':
      return 'Коррозия';
    case 'oil_leak':
      return 'Подтёк масла';
    case 'oxidation':
    case 'contact_oxidation':
    case 'electrical_oxidation':
      return 'Окисление контактов';
    case 'paint_peeling':
      return 'Отслоение краски';
    case 'mechanical_damage':
    case 'damage':
      return 'Механическое повреждение';
    case 'crack':
    case 'chip':
    case 'crack_chip':
      return 'Трещина / скол';
    case 'texture_anomaly':
      return 'Аномалия текстуры поверхности';
    case 'guard_missing':
    case 'missing_guard':
    case 'guard_removed':
      return 'Отсутствует защитный кожух';
    case 'guard_damage':
      return 'Повреждение кожуха муфты';
    case 'fog':
      return 'Запотевание стекла манометра';
    default:
      return componentLabel(code);
  }
}

enum DefectCategoryFilter { all, critical, visual, diagnostic }

/// Единая запись журнала дефектов (из осмотров, CV/AI-признаков и диагностик).
class DefectJournalEntry {
  const DefectJournalEntry({
    required this.id,
    required this.equipmentId,
    required this.equipmentName,
    required this.componentId,
    required this.title,
    required this.severity,
    required this.sourceLabel,
    required this.isVisual,
    this.confidence,
    this.timestamp,
    this.detail,
    this.recommendation,
  });

  final String id;
  final String equipmentId;
  final String equipmentName;
  final String componentId;
  final String title;
  final String severity; // 'critical' | 'warning' | 'info'
  final String sourceLabel;
  final bool isVisual;
  final double? confidence;
  final DateTime? timestamp;
  final String? detail;
  final String? recommendation;

  bool get isCritical => severity == 'critical' || severity == 'possible_defect';
}

/// Вкладка «Дефекты»: единый журнал дефектов, признаков и срабатываний с фильтрами.
class DefectsTabScreen extends StatefulWidget {
  const DefectsTabScreen({
    super.key,
    this.onStartInspectionTap,
  });

  final VoidCallback? onStartInspectionTap;

  @override
  State<DefectsTabScreen> createState() => _DefectsTabScreenState();
}

class _DefectsTabScreenState extends State<DefectsTabScreen> {
  static const List<String> _defaultIds = <String>['PUMP-001', 'PUMP-002'];

  bool _started = false;
  bool _loading = true;
  String? _error;
  List<DefectJournalEntry> _entries = const <DefectJournalEntry>[];
  Map<String, Equipment> _equipmentById = const <String, Equipment>{};

  String _equipmentFilter = 'ALL';
  DefectCategoryFilter _categoryFilter = DefectCategoryFilter.all;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      _load();
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final api = AppScope.of(context).api;
    try {
      List<Equipment> list;
      try {
        list = await api.fetchEquipmentList();
      } catch (_) {
        list = const <Equipment>[];
      }
      final ids = <String>{
        ..._defaultIds,
        for (final item in list)
          if (item.id.isNotEmpty) item.id,
      }.toList(growable: false);

      final equipmentMap = <String, Equipment>{};
      final collected = <DefectJournalEntry>[];
      final seenKeys = <String>{};

      for (final id in ids) {
        Equipment? equipment;
        try {
          equipment = await api.fetchEquipment(id);
          equipmentMap[id] = equipment;
        } catch (_) {
          for (final item in list) {
            if (item.id == id) {
              equipment = item;
              equipmentMap[id] = item;
              break;
            }
          }
        }
        final eqName = equipment?.name ?? id;

        // 1. Узлы в паспорте со статусом дефекта / внимания.
        if (equipment != null) {
          for (final comp in equipment.components) {
            if (comp.condition == 'possible_defect' ||
                comp.condition == 'check_required') {
              final key = 'comp:$id:${comp.name}:${comp.condition}';
              if (seenKeys.add(key)) {
                collected.add(
                  DefectJournalEntry(
                    id: key,
                    equipmentId: id,
                    equipmentName: eqName,
                    componentId: comp.name,
                    title: comp.condition == 'possible_defect'
                        ? 'Дефект узла «${componentLabel(comp.name)}»'
                        : 'Требуется проверка «${componentLabel(comp.name)}»',
                    severity: comp.condition == 'possible_defect'
                        ? 'critical'
                        : 'warning',
                    sourceLabel: 'Паспорт агрегата',
                    isVisual: false,
                    detail: comp.remainingResource?.text == null
                        ? null
                        : 'Остаточный ресурс: ${comp.remainingResource!.text}',
                    recommendation:
                        'Провести прицельный осмотр узла и сверить показания.',
                  ),
                );
              }
            }
          }
        }

        // 2. Активные диагностики.
        try {
          final diagSnapshot = await api.fetchDiagnostics(id);
          for (final diag in diagSnapshot.active) {
            final compId = diag.overlay?.componentId ?? 'pump';
            final key = 'diag:$id:${diag.ruleId}:$compId';
            if (seenKeys.add(key)) {
              final evidenceText = diag.evidence
                  .where((e) => e.matched)
                  .map((e) => e.description)
                  .join('; ');
              collected.add(
                DefectJournalEntry(
                  id: key,
                  equipmentId: id,
                  equipmentName: eqName,
                  componentId: compId,
                  title: diag.title,
                  severity: diag.severity == 'critical' ? 'critical' : 'warning',
                  sourceLabel: 'Активная диагностика',
                  isVisual: diag.ruleId.contains('visual'),
                  confidence: diag.confidence,
                  timestamp: diag.updatedAt != null
                      ? DateTime.tryParse(diag.updatedAt!)
                      : (diag.createdAt != null
                          ? DateTime.tryParse(diag.createdAt!)
                          : null),
                  detail: evidenceText.isNotEmpty ? evidenceText : diag.message,
                  recommendation: diag.recommendation,
                ),
              );
            }
          }
        } catch (_) {}

        // 3. Текущее окно видеоаналитики.
        try {
          final analytics = await api.fetchAnalytics(id);
          // У окна аналитики нет своей метки времени — показываем текущую.
          final updatedAt = DateTime.now();
          for (final comp in analytics.components) {
            for (final sign in comp.signs) {
              if (!sign.isShown) continue;
              final key = 'sign:$id:${comp.componentId}:${sign.sign}';
              if (seenKeys.add(key)) {
                collected.add(
                  DefectJournalEntry(
                    id: key,
                    equipmentId: id,
                    equipmentName: eqName,
                    componentId: comp.componentId,
                    title: sign.label.isNotEmpty
                        ? sign.label
                        : defectTypeLabel(sign.sign),
                    severity: sign.isDetected ? 'critical' : 'warning',
                    sourceLabel: sign.isDetected
                        ? 'Видеоаналитика · подтверждено'
                        : 'Видеоаналитика · единичное',
                    isVisual: true,
                    confidence: sign.confidence > 0 ? sign.confidence : null,
                    timestamp: updatedAt,
                    detail: sign.framesPresent > 0
                        ? 'Кадров с признаком: ${sign.framesPresent}'
                        : null,
                    recommendation:
                        'Проверить поверхность узла «${comp.label}» при осмотре.',
                  ),
                );
              }
            }
          }
        } catch (_) {}

        // 4. История сохранённых осмотров.
        try {
          final inspections = await api.fetchInspections(id);
          for (final record in inspections.take(12)) {
            final ts = record.parsedAt;
            // Детекции дефектов из summary
            for (final det in record.summary.detections) {
              if (!det.isDefect) continue;
              final compId = _guessComponentForDefect(record, det.className);
              final key =
                  'insp-det:$id:${record.id}:${det.className}:$compId';
              if (seenKeys.add(key)) {
                collected.add(
                  DefectJournalEntry(
                    id: key,
                    equipmentId: id,
                    equipmentName: eqName,
                    componentId: compId,
                    title: defectTypeLabel(det.className),
                    severity: det.confidence >= 0.7 ? 'critical' : 'warning',
                    sourceLabel: 'Осмотр №${record.id} · CV/AI',
                    isVisual: true,
                    confidence: det.confidence,
                    timestamp: ts,
                    recommendation: _defaultRecommendationFor(det.className),
                  ),
                );
              }
            }
            // Диагностики на момент осмотра
            for (final diag in record.diagnostics) {
              final compId = diag.overlay?.componentId ?? 'pump';
              final key = 'insp-diag:$id:${record.id}:${diag.ruleId}:$compId';
              if (seenKeys.add(key)) {
                collected.add(
                  DefectJournalEntry(
                    id: key,
                    equipmentId: id,
                    equipmentName: eqName,
                    componentId: compId,
                    title: diag.title,
                    severity:
                        diag.severity == 'critical' ? 'critical' : 'warning',
                    sourceLabel: 'Осмотр №${record.id} · правило',
                    isVisual: diag.ruleId.contains('visual'),
                    confidence: diag.confidence,
                    timestamp: ts,
                    detail: diag.message,
                    recommendation: diag.recommendation,
                  ),
                );
              }
            }
          }
        } catch (_) {}
      }

      if (equipmentMap.isEmpty) {
        throw ApiException('Нет связи с сервером. Проверьте подключение.');
      }

      collected.sort((a, b) {
        if (a.isCritical != b.isCritical) return a.isCritical ? -1 : 1;
        final ta = a.timestamp;
        final tb = b.timestamp;
        if (ta != null && tb != null) return tb.compareTo(ta);
        if (ta != null) return -1;
        if (tb != null) return 1;
        return (b.confidence ?? 0).compareTo(a.confidence ?? 0);
      });

      if (!mounted) return;
      setState(() {
        _equipmentById = equipmentMap;
        _entries = collected;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Не удалось загрузить журнал дефектов';
        _loading = false;
      });
    }
  }

  String _guessComponentForDefect(InspectionRecord record, String defectClass) {
    for (final st in record.summary.componentsStatus) {
      if (st.defects.contains(defectClass)) {
        return st.componentId;
      }
    }
    return 'pump';
  }

  String _defaultRecommendationFor(String code) {
    switch (code) {
      case 'corrosion':
        return 'Зачистить и окрасить поверхность, проверить толщину стенки.';
      case 'oil_leak':
        return 'Проверить уплотнение и уровень масла, устранить течь.';
      case 'oxidation':
        return 'Зачистить контакты, протянуть клеммные соединения.';
      case 'crack':
        return 'Остановить агрегат и провести дефектоскопию корпуса.';
      default:
        return 'Осмотреть узел на месте и подтвердить состояние.';
    }
  }

  List<DefectJournalEntry> get _filteredEntries {
    return _entries.where((entry) {
      if (_equipmentFilter != 'ALL' && entry.equipmentId != _equipmentFilter) {
        return false;
      }
      switch (_categoryFilter) {
        case DefectCategoryFilter.all:
          return true;
        case DefectCategoryFilter.critical:
          return entry.isCritical;
        case DefectCategoryFilter.visual:
          return entry.isVisual;
        case DefectCategoryFilter.diagnostic:
          return !entry.isVisual;
      }
    }).toList(growable: false);
  }

  void _openEntry(DefectJournalEntry entry) {
    final equipment = _equipmentById[entry.equipmentId];
    if (equipment != null) {
      ComponentInfo? comp;
      for (final item in equipment.components) {
        if (item.name == entry.componentId) {
          comp = item;
          break;
        }
      }
      if (comp != null) {
        Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => ComponentDetailScreen(
              equipment: equipment,
              component: comp!,
              diagnostics: const <DiagnosticEvent>[],
            ),
          ),
        );
        return;
      }
    }
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => EquipmentScreen(equipmentId: entry.equipmentId),
      ),
    );
  }

  void _resetFilters() {
    setState(() {
      _equipmentFilter = 'ALL';
      _categoryFilter = DefectCategoryFilter.all;
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final filtered = _filteredEntries;
    final hasActiveFilters = _equipmentFilter != 'ALL' ||
        _categoryFilter != DefectCategoryFilter.all;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Журнал дефектов'),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 4),
            child: Center(child: ConnectionIndicator()),
          ),
          const ThemeModeButton(),
          IconButton(
            tooltip: 'Обновить журнал',
            onPressed: _loading ? null : _load,
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
          _buildFilterBar(palette),
          Divider(color: palette.outline, height: 1),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? _buildErrorState(palette)
                    : filtered.isEmpty
                        ? _buildEmptyState(palette, hasActiveFilters)
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.separated(
                              padding:
                                  const EdgeInsets.fromLTRB(16, 12, 16, 24),
                              itemCount: filtered.length,
                              separatorBuilder: (_, _) =>
                                  const SizedBox(height: 12),
                              itemBuilder: (context, index) => _DefectEntryCard(
                                entry: filtered[index],
                                onTap: () => _openEntry(filtered[index]),
                              ),
                            ),
                          ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilterBar(AppPalette palette) {
    return Container(
      color: palette.surface,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _FilterChipItem(
                  key: const Key('defect-filter-all-eq'),
                  label: 'Все (${_entries.length})',
                  selected: _equipmentFilter == 'ALL',
                  onTap: () => setState(() => _equipmentFilter = 'ALL'),
                ),
                const SizedBox(width: 8),
                for (final eqId in _defaultIds) ...[
                  _FilterChipItem(
                    key: Key('defect-filter-$eqId'),
                    label: eqId,
                    selected: _equipmentFilter == eqId,
                    onTap: () => setState(() => _equipmentFilter = eqId),
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          const SizedBox(height: 8),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _FilterChipItem(
                  key: const Key('defect-cat-all'),
                  label: 'Все типы',
                  selected: _categoryFilter == DefectCategoryFilter.all,
                  onTap: () => setState(
                    () => _categoryFilter = DefectCategoryFilter.all,
                  ),
                ),
                const SizedBox(width: 8),
                _FilterChipItem(
                  key: const Key('defect-cat-critical'),
                  label: 'Критичные',
                  selected: _categoryFilter == DefectCategoryFilter.critical,
                  onTap: () => setState(
                    () => _categoryFilter = DefectCategoryFilter.critical,
                  ),
                ),
                const SizedBox(width: 8),
                _FilterChipItem(
                  key: const Key('defect-cat-visual'),
                  label: 'Визуальные (CV/AI)',
                  selected: _categoryFilter == DefectCategoryFilter.visual,
                  onTap: () => setState(
                    () => _categoryFilter = DefectCategoryFilter.visual,
                  ),
                ),
                const SizedBox(width: 8),
                _FilterChipItem(
                  key: const Key('defect-cat-diagnostic'),
                  label: 'Диагностика',
                  selected: _categoryFilter == DefectCategoryFilter.diagnostic,
                  onTap: () => setState(
                    () => _categoryFilter = DefectCategoryFilter.diagnostic,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState(AppPalette palette, bool hasActiveFilters) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              hasActiveFilters
                  ? Icons.filter_alt_off_outlined
                  : Icons.verified_outlined,
              size: 48,
              color: hasActiveFilters ? palette.textMuted : palette.green,
            ),
            const SizedBox(height: 12),
            Text(
              hasActiveFilters
                  ? 'По выбранному фильтру записей нет'
                  : 'Дефектов не обнаружено',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: palette.text,
                fontSize: 20,
                fontWeight: FontWeight.w700,
                height: 1.3,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              hasActiveFilters
                  ? 'Сбросьте фильтр агрегата или категории, чтобы увидеть все записи журнала.'
                  : 'Проведите AR-осмотр оборудования с камеры или фото, чтобы проверить узлы.',
              textAlign: TextAlign.center,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.textSecondary,
                fontSize: 15,
                height: 1.35,
              ),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              key: const Key('defects-empty-action'),
              onPressed: hasActiveFilters
                  ? _resetFilters
                  : (widget.onStartInspectionTap ?? _load),
              icon: Icon(
                hasActiveFilters
                    ? Icons.filter_alt_off_outlined
                    : Icons.qr_code_scanner,
              ),
              label: Text(
                hasActiveFilters ? 'Сбросить фильтр' : 'Перейти к осмотру',
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildErrorState(AppPalette palette) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.warning_amber_rounded, size: 48, color: palette.yellow),
            const SizedBox(height: 12),
            Text(
              'Нет связи с сервером',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: palette.text,
                fontSize: 20,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '$_error\nРанее сохранённые на сервере данные не потеряны.',
              textAlign: TextAlign.center,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: palette.textSecondary,
                fontSize: 15,
                height: 1.35,
              ),
            ),
            const SizedBox(height: 18),
            OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              label: const Text('Повторить'),
            ),
          ],
        ),
      ),
    );
  }
}

class _FilterChipItem extends StatelessWidget {
  const _FilterChipItem({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final selectedBg =
        palette.isDark ? const Color(0xFF0F2135) : const Color(0xFFDCEAFB);
    return Material(
      color: selected ? selectedBg : palette.surfaceHigh,
      borderRadius: BorderRadius.circular(999),
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: Container(
          height: 40,
          padding: const EdgeInsets.symmetric(horizontal: 14),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: selected ? palette.primary : palette.outline,
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? palette.primary : palette.text,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ),
    );
  }
}

class _DefectEntryCard extends StatelessWidget {
  const _DefectEntryCard({
    required this.entry,
    required this.onTap,
  });

  final DefectJournalEntry entry;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = entry.isCritical ? palette.red : palette.yellow;
    final bgColor = entry.isCritical
        ? (palette.isDark ? const Color(0xFF1A1215) : palette.errorSurface)
        : palette.surface;
    final timeText = entry.timestamp == null
        ? 'Активно сейчас'
        : DateFormat('dd.MM.yyyy HH:mm').format(entry.timestamp!.toLocal());
    final confText = entry.confidence == null || entry.confidence! <= 0
        ? null
        : '${(entry.confidence! * 100).round()}%';

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Container(
          padding: const EdgeInsets.fromLTRB(12, 14, 16, 14),
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
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${entry.equipmentId} · ${componentLabel(entry.componentId)}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.4,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  StatusChip.status(
                    status: entry.isCritical ? 'possible_defect' : 'check_required',
                    label: entry.isCritical ? 'Дефект' : 'Внимание',
                    dense: true,
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                entry.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: palette.text,
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  height: 1.25,
                ),
              ),
              if (entry.detail != null && entry.detail!.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                  entry.detail!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.textSecondary,
                    fontSize: 14,
                    height: 1.35,
                  ),
                ),
              ],
              if (entry.recommendation != null &&
                  entry.recommendation!.isNotEmpty) ...[
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
                        entry.recommendation!,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.text,
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          height: 1.35,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
              const SizedBox(height: 10),
              Row(
                children: [
                  Icon(Icons.schedule, size: 14, color: palette.textMuted),
                  const SizedBox(width: 4),
                  Text(
                    timeText,
                    style: TextStyle(
                      color: palette.textMuted,
                      fontSize: 12,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '· ${entry.sourceLabel}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ),
                  if (confText != null) ...[
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: palette.surfaceHigh,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        'Уверенность $confText',
                        style: TextStyle(
                          color: color,
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
