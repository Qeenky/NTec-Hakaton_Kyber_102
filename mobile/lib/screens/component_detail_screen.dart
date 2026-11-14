import 'package:intl/intl.dart';
import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/analytics.dart';
import '../models/component_info.dart';
import '../models/diagnostic.dart';
import '../models/equipment.dart';
import '../models/inspection.dart';
import '../models/labels.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/metric_tile.dart';
import '../widgets/status_chip.dart';
import '../widgets/visual_signs.dart';

/// Детальная карточка узла: ресурс, состояние, последняя проверка.
class ComponentDetailScreen extends StatefulWidget {
  const ComponentDetailScreen({
    super.key,
    required this.equipment,
    required this.component,
    required this.diagnostics,
    this.visible,
    this.analyticsComponent,
  });

  final Equipment equipment;
  final ComponentInfo component;
  final List<DiagnosticEvent> diagnostics;
  final AnalyticsComponent? analyticsComponent;

  /// `null` — нет кадра (карточка оборудования), `false` — узел не в кадре.
  final bool? visible;

  @override
  State<ComponentDetailScreen> createState() => _ComponentDetailScreenState();
}

class _ComponentDetailScreenState extends State<ComponentDetailScreen> {
  bool _started = false;
  bool _loading = true;
  String? _error;
  InspectionRecord? _lastInspection;
  int _inspectionsCount = 0;
  AnalyticsComponent? _analyticsComponent;
  bool _signsLoading = false;
  String? _signsError;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _analyticsComponent = widget.analyticsComponent;
    _loadHistory();
    if (_analyticsComponent == null) {
      _loadSigns();
    }
  }

  Future<void> _loadSigns() async {
    setState(() {
      _signsLoading = true;
      _signsError = null;
    });
    try {
      final analytics = await AppScope.of(context).api
          .fetchAnalytics(widget.equipment.id);
      if (!mounted) return;
      AnalyticsComponent? found;
      for (final item in analytics.components) {
        if (item.componentId == widget.component.name) {
          found = item;
          break;
        }
      }
      setState(() {
        _analyticsComponent = found;
        _signsLoading = false;
      });
    } on ApiException catch (exception) {
      if (!mounted) return;
      setState(() {
        _signsError = exception.message;
        _signsLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _signsError = 'Не удалось загрузить визуальные признаки';
        _signsLoading = false;
      });
    }
  }

  Future<void> _loadHistory() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final records = await AppScope.of(context).api
          .fetchInspections(widget.equipment.id);
      if (!mounted) return;
      InspectionRecord? last;
      for (final record in records) {
        if (record.summary.componentsStatus.any(
          (item) => item.componentId == widget.component.name,
        )) {
          last = record;
          break;
        }
      }
      setState(() {
        _lastInspection = last;
        _inspectionsCount = records.length;
        _loading = false;
      });
    } on ApiException catch (exception) {
      if (!mounted) return;
      setState(() {
        _error = exception.message;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Не удалось загрузить историю';
        _loading = false;
      });
    }
  }

  Widget _buildSigns() {
    if (_signsLoading) {
      return const Row(
        children: [
          SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          SizedBox(width: 10),
          Text(
            'Загружаем визуальные признаки…',
            style: TextStyle(color: AppColors.textMuted, fontSize: 12.5),
          ),
        ],
      );
    }
    if (_signsError != null) {
      return Row(
        children: [
          Expanded(
            child: Text(
              _signsError!,
              style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
            ),
          ),
          TextButton(onPressed: _loadSigns, child: const Text('Повторить')),
        ],
      );
    }
    final signs = _analyticsComponent?.signs ?? const <VisualSign>[];
    if (_analyticsComponent == null) {
      return const Text(
        'Нет данных о визуальных признаках',
        style: TextStyle(color: AppColors.textMuted, fontSize: 12.5),
      );
    }
    if (signs.isEmpty) {
      return const Text(
        'Визуальные признаки не обнаружены',
        style: TextStyle(color: AppColors.textMuted, fontSize: 12.5),
      );
    }
    if (!signs.any((sign) => sign.isShown || sign.isNotVisible)) {
      return const Text(
        'Визуальные признаки не обнаружены',
        style: TextStyle(color: AppColors.textMuted, fontSize: 12.5),
      );
    }
    return VisualSignsTable(signs: signs);
  }

  String? get _recommendation {
    for (final event in widget.diagnostics) {
      if (event.overlay?.componentId == widget.component.name &&
          event.recommendation != null &&
          event.recommendation!.isNotEmpty) {
        return event.recommendation;
      }
    }
    for (final event in widget.diagnostics) {
      if (event.recommendation != null &&
          event.recommendation!.isNotEmpty &&
          event.title.toLowerCase().contains(
            componentLabel(widget.component.name).toLowerCase(),
          )) {
        return event.recommendation;
      }
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final component = widget.component;
    final visible = widget.visible;
    final resource = component.remainingResource;
    final color = visible == false
        ? AppColors.grey
        : AppColors.byName(component.condition);

    return Scaffold(
      appBar: AppBar(
        title: Text(componentLabel(component.name)),
        actions: [
          IconButton(
            tooltip: 'Обновить',
            onPressed: _loading ? null : _loadHistory,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          Panel(
            borderColor: color.withValues(alpha: 0.5),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        componentLabel(component.name),
                        style: const TextStyle(
                          fontSize: 23,
                          fontWeight: FontWeight.w800,
                          height: 1.15,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (visible == false)
                      const StatusChip(
                        label: 'не в кадре',
                        color: AppColors.grey,
                      )
                    else
                      StatusChip.status(
                        status: component.condition,
                        label: componentStatusLabel(component.condition),
                      ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  '${widget.equipment.name} · ${widget.equipment.id}',
                  style: const TextStyle(
                    color: AppColors.textMuted,
                    fontSize: 12.5,
                  ),
                ),
                if (visible == false) ...[
                  const SizedBox(height: 10),
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: AppColors.grey.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                        color: AppColors.grey.withValues(alpha: 0.4),
                      ),
                    ),
                    child: const Row(
                      children: [
                        Icon(
                          Icons.visibility_off_outlined,
                          size: 16,
                          color: AppColors.textMuted,
                        ),
                        SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            'Узел не попал в кадр. Показания состояния и '
                            'остатка ресурса не подставляются (—).',
                            style: TextStyle(
                              color: AppColors.textMuted,
                              fontSize: 12,
                              height: 1.35,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: MetricTile(
                        label: 'Остаток ресурса',
                        value: visible == false
                            ? '—'
                            : (resource?.valueYears == null
                                  ? '—'
                                  : '≈ ${resource!.valueYears!.toStringAsFixed(1)}'),
                        unit: visible == false || resource?.valueYears == null
                            ? null
                            : 'лет',
                        color: visible == false
                            ? AppColors.grey
                            : (resource?.valueYears != null &&
                                      resource!.valueYears! <= 0
                                  ? AppColors.red
                                  : null),
                        caption: resource?.isEstimate == true
                            ? 'оценка, не гарантия'
                            : null,
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: MetricTile(
                        label: 'Состояние',
                        value: visible == false
                            ? '—'
                            : componentStatusLabel(component.condition),
                        color: color,
                        compact: true,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          const _SectionTitle('ПАСПОРТ УЗЛА'),
          const SizedBox(height: 8),
          Panel(
            child: Column(
              children: [
                _InfoRow(
                  label: 'Нормативный срок',
                  value: component.normativeLifeYears == null
                      ? '—'
                      : '${component.normativeLifeYears!.toStringAsFixed(0)} лет',
                ),
                _InfoRow(
                  label: 'Дата установки',
                  value: component.installedDate ?? '—',
                ),
                _InfoRow(
                  label: 'Тип записи',
                  value: component.kind == 'defect'
                      ? 'Дефект'
                      : 'Комплектующая',
                ),
                _InfoRow(
                  label: 'Остаточный ресурс',
                  value: resource?.text ?? 'н/д',
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          const _SectionTitle('ВИЗУАЛЬНЫЕ ПРИЗНАКИ'),
          const SizedBox(height: 8),
          Panel(child: _buildSigns()),
          const SizedBox(height: 16),
          const _SectionTitle('ПОСЛЕДНЯЯ ПРОВЕРКА'),
          const SizedBox(height: 8),
          Panel(
            child: _loading
                ? const Row(
                    children: [
                      SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                      SizedBox(width: 10),
                      Text(
                        'Загружаем историю осмотров…',
                        style: TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 12.5,
                        ),
                      ),
                    ],
                  )
                : _error != null
                ? Row(
                    children: [
                      Expanded(
                        child: Text(
                          _error!,
                          style: const TextStyle(
                            color: AppColors.red,
                            fontSize: 12.5,
                          ),
                        ),
                      ),
                      TextButton(
                        onPressed: _loadHistory,
                        child: const Text('Повторить'),
                      ),
                    ],
                  )
                : Row(
                    children: [
                      const Icon(
                        Icons.fact_check_outlined,
                        size: 18,
                        color: AppColors.accent,
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              _lastInspection?.parsedAt == null
                                  ? 'Нет данных об осмотрах узла'
                                  : DateFormat('dd.MM.yyyy HH:mm').format(
                                      _lastInspection!.parsedAt!.toLocal(),
                                    ),
                              style: const TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              _lastInspection == null
                                  ? 'в истории нет сохранённых осмотров'
                                  : 'осмотр №${_lastInspection!.id} · '
                                        'всего осмотров: $_inspectionsCount',
                              style: const TextStyle(
                                color: AppColors.textMuted,
                                fontSize: 11.5,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
          ),
          if (_recommendation != null) ...[
            const SizedBox(height: 16),
            const _SectionTitle('РЕКОМЕНДАЦИЯ'),
            const SizedBox(height: 8),
            Panel(
              borderColor: AppColors.accent.withValues(alpha: 0.45),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(
                    Icons.build_circle_outlined,
                    color: AppColors.accent,
                    size: 20,
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      _recommendation!,
                      style: const TextStyle(fontSize: 13.5, height: 1.35),
                    ),
                  ),
                ],
              ),
            ),
          ],
          if (visible != false && resource?.isEstimate == true) ...[
            const SizedBox(height: 12),
            Text(
              'Остаточный ресурс — оценка: максимальный срок минус возраст, '
              'умноженный на коэффициент состояния (норма 1.0, внимание 0.7, '
              'дефект 0.4). Не заменяет заключение специалиста.',
              style: TextStyle(
                color: AppColors.textMuted.withValues(alpha: 0.9),
                fontSize: 11.5,
                height: 1.35,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 150,
            child: Text(
              label,
              style: const TextStyle(
                color: AppColors.textMuted,
                fontSize: 12.5,
              ),
            ),
          ),
          Expanded(child: Text(value, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Text(
      text,
      style: TextStyle(
        color: palette.text,
        fontSize: 14,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.3,
      ),
    );
  }
}
