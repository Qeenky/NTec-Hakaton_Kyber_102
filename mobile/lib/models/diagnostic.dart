import 'json_utils.dart';

/// Одно условие в `evidence` диагностики.
/// В `/ws/frame` поле `op`, в REST — `condition`; поддерживаем оба.
class EvidenceItem {
  const EvidenceItem({
    required this.matched,
    this.condition,
    this.signal,
    this.op,
    this.actual,
    this.baseline,
    this.threshold,
    this.weight,
    this.score,
  });

  final bool matched;
  final String? condition;
  final String? signal;
  final String? op;
  final Object? actual;
  final Object? baseline;
  final Object? threshold;
  final double? weight;
  final double? score;

  factory EvidenceItem.fromJson(Map<String, dynamic> json) {
    return EvidenceItem(
      matched: asBool(json['matched']) ?? false,
      condition: asString(json['condition']),
      signal: asString(json['signal']),
      op: asString(json['op']),
      actual: json['actual'],
      baseline: json['baseline'],
      threshold: json['threshold'],
      weight: asDouble(json['weight']),
      score: asDouble(json['score']),
    );
  }

  static String _value(Object? value) {
    if (value == null) return '—';
    if (value is num) {
      return value.abs() >= 100
          ? value.toStringAsFixed(0)
          : value.toStringAsFixed(2);
    }
    return value.toString();
  }

  /// Человекочитаемое условие для экрана результата.
  String get description {
    if (condition != null && condition!.isNotEmpty) return condition!;
    final name = signal != null ? signalLabelSafe(signal!) : 'сигнал';
    final thresholdText = _value(threshold);
    final actualText = _value(actual);
    final baselineText = baseline == null ? null : _value(baseline);
    final opText = switch (op) {
      'gt' => '>',
      'gte' => '≥',
      'lt' => '<',
      'lte' => '≤',
      'deviation_pct' => 'отклонение от',
      'trend_up' => 'рост',
      'trend_down' => 'снижение',
      'unbalance_pct' => 'дисбаланс',
      'in' => 'входит в',
      'crosses' => 'пересекает',
      'has_signal' => 'есть сигнал',
      'cv' => 'CV-признак',
      'any' => 'любое из',
      _ => op ?? '',
    };
    final buffer = StringBuffer('$name $opText $thresholdText');
    if (baselineText != null) buffer.write(' (норма $baselineText)');
    buffer.write(', факт $actualText');
    return buffer.toString();
  }
}

String signalLabelSafe(String key) {
  const labels = {
    'vibration_rms': 'вибрация',
    'vibration_axial': 'осевая вибрация',
    'vibration_hf': 'ВЧ-вибрация',
    'vibration_lf_1x': 'НЧ 1×',
    'temperature_bearing': 'темп. подшипника',
    'temperature_motor': 'темп. двигателя',
    'current_a': 'ток A',
    'pressure_inlet': 'давление вход',
    'pressure_outlet': 'давление выход',
    'efficiency': 'КПД',
    'runtime_hours': 'наработка',
    'oil_level': 'уровень масла',
    'oil_color': 'цвет масла',
    'noise_db': 'шум',
    'manometer_ocr': 'манометр',
  };
  return labels[key] ?? key;
}

class DiagnosticOverlay {
  const DiagnosticOverlay({
    this.componentId,
    this.marker,
    this.color,
    this.label,
  });

  final String? componentId;
  final String? marker;
  final String? color;
  final String? label;

  factory DiagnosticOverlay.fromJson(Map<String, dynamic> json) {
    return DiagnosticOverlay(
      componentId: asString(json['component_id']),
      marker: asString(json['marker']),
      color: asString(json['color']),
      label: asString(json['label']),
    );
  }
}

/// Событие из `active_diagnostics` / `history`.
class DiagnosticEvent {
  const DiagnosticEvent({
    required this.ruleId,
    required this.title,
    required this.severity,
    required this.status,
    required this.evidence,
    this.id,
    this.confidence,
    this.message,
    this.recommendation,
    this.overlay,
    this.createdAt,
    this.updatedAt,
    this.resolvedAt,
  });

  final int? id;
  final String ruleId;
  final String title;
  final String severity;
  final String status;
  final double? confidence;
  final String? message;
  final String? recommendation;
  final List<EvidenceItem> evidence;
  final DiagnosticOverlay? overlay;
  final String? createdAt;
  final String? updatedAt;
  final String? resolvedAt;

  factory DiagnosticEvent.fromJson(Map<String, dynamic> json) {
    final rawOverlay = json['overlay'];
    return DiagnosticEvent(
      id: asInt(json['id']),
      ruleId: asString(json['rule_id']) ?? '',
      title: asString(json['title']) ?? 'Диагностика',
      severity: asString(json['severity']) ?? 'info',
      status: asString(json['status']) ?? 'active',
      confidence: asDouble(json['confidence']),
      message: asString(json['message']),
      recommendation: asString(json['recommendation']),
      evidence: asMapList(json['evidence'])
          .map(EvidenceItem.fromJson)
          .toList(growable: false),
      overlay: rawOverlay is Map
          ? DiagnosticOverlay.fromJson(asMap(rawOverlay))
          : null,
      createdAt: asString(json['created_at']),
      updatedAt: asString(json['updated_at']),
      resolvedAt: asString(json['resolved_at']),
    );
  }

  bool get isCritical => severity == 'critical';
}

/// Правило, которое молчит, и причина (`blocked_diagnostics`).
class BlockedDiagnostic {
  const BlockedDiagnostic({
    required this.ruleId,
    required this.title,
    required this.severity,
    required this.blockedBy,
    required this.reason,
  });

  final String ruleId;
  final String title;
  final String severity;
  final String? blockedBy;
  final String? reason;

  factory BlockedDiagnostic.fromJson(Map<String, dynamic> json) {
    return BlockedDiagnostic(
      ruleId: asString(json['rule_id']) ?? '',
      title: asString(json['title']) ?? asString(json['rule_id']) ?? '',
      severity: asString(json['severity']) ?? 'info',
      blockedBy: asString(json['blocked_by']),
      reason: asString(json['reason']),
    );
  }
}

/// Правило, которое посчиталось, но осталось в пределах нормы.
class NormalDiagnostic {
  const NormalDiagnostic({
    required this.ruleId,
    required this.title,
    required this.severity,
    required this.confidence,
    this.activateConfidence,
    this.matched = false,
    this.conditionsMatched = 0,
    this.conditionsTotal = 0,
    this.suppressedBy,
  });

  final String ruleId;
  final String title;
  final String severity;
  final double confidence;
  final double? activateConfidence;
  final bool matched;
  final int conditionsMatched;
  final int conditionsTotal;
  final String? suppressedBy;

  factory NormalDiagnostic.fromJson(Map<String, dynamic> json) {
    return NormalDiagnostic(
      ruleId: asString(json['rule_id']) ?? '',
      title: asString(json['title']) ?? asString(json['rule_id']) ?? '',
      severity: asString(json['severity']) ?? 'info',
      confidence: asDouble(json['confidence']) ?? 0,
      activateConfidence: asDouble(json['activate_confidence']),
      matched: asBool(json['matched']) ?? false,
      conditionsMatched: asInt(json['conditions_matched']) ?? 0,
      conditionsTotal: asInt(json['conditions_total']) ?? 0,
      suppressedBy: asString(json['suppressed_by']),
    );
  }
}

class CameraState {
  const CameraState({
    required this.available,
    this.source,
    this.ageSec,
    this.ttlSec,
    this.ocr,
  });

  final bool available;
  final String? source;
  final double? ageSec;
  final int? ttlSec;
  final Map<String, dynamic>? ocr;

  factory CameraState.fromJson(Map<String, dynamic> json) {
    final rawOcr = json['ocr'];
    return CameraState(
      available: asBool(json['available']) ?? false,
      source: asString(json['source']),
      ageSec: asDouble(json['age_sec']),
      ttlSec: asInt(json['ttl_sec']),
      ocr: rawOcr is Map ? asMap(rawOcr) : null,
    );
  }
}

/// Ответ `/equipment/{id}/diagnostics`.
class DiagnosticsSnapshot {
  const DiagnosticsSnapshot({
    required this.equipmentId,
    required this.active,
    required this.history,
    required this.blocked,
    this.normal = const <NormalDiagnostic>[],
    this.scenario,
    this.camera,
  });

  final String equipmentId;
  final String? scenario;
  final CameraState? camera;
  final List<DiagnosticEvent> active;
  final List<DiagnosticEvent> history;
  final List<BlockedDiagnostic> blocked;
  final List<NormalDiagnostic> normal;

  factory DiagnosticsSnapshot.fromJson(Map<String, dynamic> json) {
    final rawCamera = json['camera'];
    return DiagnosticsSnapshot(
      equipmentId: asString(json['equipment_id']) ?? '',
      scenario: asString(json['scenario']),
      camera: rawCamera is Map ? CameraState.fromJson(asMap(rawCamera)) : null,
      active: asMapList(json['active'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      history: asMapList(json['history'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      blocked: asMapList(json['blocked'])
          .map(BlockedDiagnostic.fromJson)
          .toList(growable: false),
      normal: asMapList(json['normal'])
          .map(NormalDiagnostic.fromJson)
          .toList(growable: false),
    );
  }

  static const empty = DiagnosticsSnapshot(
    equipmentId: '',
    active: <DiagnosticEvent>[],
    history: <DiagnosticEvent>[],
    blocked: <BlockedDiagnostic>[],
    normal: <NormalDiagnostic>[],
  );
}
