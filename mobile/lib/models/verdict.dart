import 'json_utils.dart';
import 'labels.dart';

const String verdictDisclaimer =
    'Отсутствие видимых дефектов не подтверждает исправность оборудования.';

/// Наблюдение по одному узлу для расчёта итогового вердикта.
class VerdictObservation {
  const VerdictObservation({
    required this.componentId,
    required this.status,
    this.seen = true,
  });

  final String componentId;
  final String status;
  final bool seen;

  int get _rank {
    switch (status) {
      case 'possible_defect':
        return 3;
      case 'check_required':
        return 2;
      case 'ok':
        return 1;
      default:
        return 0;
    }
  }

  VerdictObservation merge(VerdictObservation other) {
    final best = other._rank > _rank ? other : this;
    return VerdictObservation(
      componentId: componentId,
      status: best.status,
      seen: seen || other.seen,
    );
  }
}

/// Итог осмотра: серверный `verdict` или вычисленный на клиенте fallback.
class InspectionVerdict {
  const InspectionVerdict({
    required this.status,
    required this.reason,
    required this.componentsWithIssues,
    required this.componentsNotSeen,
    this.disclaimer = verdictDisclaimer,
  });

  /// `defects_found` | `attention_required` | `no_visible_defects` |
  /// `insufficient_data`.
  final String status;
  final String reason;
  final List<String> componentsWithIssues;
  final List<String> componentsNotSeen;
  final String disclaimer;

  factory InspectionVerdict.fromJson(Map<String, dynamic> json) {
    var status = asString(json['status']) ?? 'insufficient_data';
    var reason = asString(json['reason']) ?? _defaultReason(status);
    final issues = asStringList(json['components_with_issues']);
    final notSeen = asStringList(json['components_not_seen']);

    // Даже серверный «норма» не должен скрывать проблемы/невидимые узлы.
    if (status == 'no_visible_defects' &&
        (issues.isNotEmpty || notSeen.isNotEmpty)) {
      if (issues.isNotEmpty) {
        status = 'attention_required';
        reason = 'Есть узлы, требующие проверки.';
      } else {
        status = 'insufficient_data';
        reason = 'Не все узлы были видны в кадре: вывод недостаточен.';
      }
    }

    return InspectionVerdict(
      status: status,
      reason: reason,
      componentsWithIssues: issues,
      componentsNotSeen: notSeen,
      disclaimer: asString(json['disclaimer']) ?? verdictDisclaimer,
    );
  }

  static InspectionVerdict? fromJsonOrNull(Object? raw) {
    if (raw is! Map) return null;
    final map = asMap(raw);
    final status = asString(map['status']);
    if (status == null || status.isEmpty) return null;
    return InspectionVerdict.fromJson(map);
  }

  bool get isGood => status == 'no_visible_defects';

  /// Правила fallback: никогда не выдавать «норму», если данных недостаточно.
  static InspectionVerdict resolve({
    required Iterable<String> roster,
    required Iterable<VerdictObservation> observations,
    bool hasFrame = true,
    bool hasDefectEvidence = false,
  }) {
    final merged = <String, VerdictObservation>{};
    for (final observation in observations) {
      if (observation.componentId.isEmpty) continue;
      final previous = merged[observation.componentId];
      merged[observation.componentId] =
          previous == null ? observation : previous.merge(observation);
    }
    for (final id in roster) {
      if (id.isEmpty) continue;
      merged.putIfAbsent(
        id,
        () => VerdictObservation(
          componentId: id,
          status: 'insufficient_data',
          seen: false,
        ),
      );
    }

    final all = merged.values.toList(growable: false);
    final issues = all
        .where((item) =>
            item.status == 'possible_defect' ||
            item.status == 'check_required')
        .map((item) => item.componentId)
        .toSet()
        .toList(growable: false);
    final notSeen = all
        .where((item) =>
            (!item.seen || item.status == 'insufficient_data') &&
            !issues.contains(item.componentId))
        .map((item) => item.componentId)
        .toSet()
        .toList(growable: false);

    if (!hasFrame) {
      return InspectionVerdict(
        status: 'insufficient_data',
        reason: 'Кадры не получены: сделать вывод о состоянии нельзя.',
        componentsWithIssues: issues,
        componentsNotSeen: all.map((item) => item.componentId).toList(),
      );
    }
    if (all.isEmpty) {
      return const InspectionVerdict(
        status: 'insufficient_data',
        reason: 'Нет данных о состоянии узлов.',
        componentsWithIssues: <String>[],
        componentsNotSeen: <String>[],
      );
    }
    if (issues.isNotEmpty) {
      final labels = issues.map(componentLabel).join(', ');
      return InspectionVerdict(
        status: 'attention_required',
        reason: 'На узлах ($labels) есть признаки, требующие проверки.',
        componentsWithIssues: issues,
        componentsNotSeen: notSeen,
      );
    }
    if (hasDefectEvidence) {
      return InspectionVerdict(
        status: 'attention_required',
        reason: 'Есть видимые признаки дефекта: требуется проверка.',
        componentsWithIssues: issues,
        componentsNotSeen: notSeen,
      );
    }
    if (notSeen.isNotEmpty) {
      final labels = notSeen.map(componentLabel).join(', ');
      return InspectionVerdict(
        status: 'insufficient_data',
        reason: 'Не видны узлы: $labels. Данных для вывода недостаточно.',
        componentsWithIssues: issues,
        componentsNotSeen: notSeen,
      );
    }
    return const InspectionVerdict(
      status: 'no_visible_defects',
      reason: 'На видимых узлах признаков дефектов не обнаружено.',
      componentsWithIssues: <String>[],
      componentsNotSeen: <String>[],
    );
  }

  static String _defaultReason(String status) {
    switch (status) {
      case 'defects_found':
        return 'Обнаружены признаки дефекта на узлах оборудования.';
      case 'attention_required':
        return 'Есть узлы, требующие проверки.';
      case 'no_visible_defects':
        return 'На видимых узлах признаков дефектов не обнаружено.';
      default:
        return 'Недостаточно данных для вывода.';
    }
  }
}
