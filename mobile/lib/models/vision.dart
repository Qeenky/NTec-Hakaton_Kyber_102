import 'json_utils.dart';

/// Признаки, которые ищет локальная VLM. `null` — признак не видно.
class VisionSigns {
  const VisionSigns({
    this.corrosion,
    this.paintPeeling,
    this.oilLeak,
    this.mechanicalDamage,
  });

  final bool? corrosion;
  final bool? paintPeeling;
  final bool? oilLeak;
  final bool? mechanicalDamage;

  factory VisionSigns.fromJson(Map<String, dynamic> json) {
    return VisionSigns(
      corrosion: asBool(json['corrosion']),
      paintPeeling: asBool(json['paint_peeling']),
      oilLeak: asBool(json['oil_leak']),
      mechanicalDamage: asBool(json['mechanical_damage']),
    );
  }

  static const unknown = VisionSigns();
}

/// Ответ `POST /vision/check`.
class VisionCheck {
  const VisionCheck({
    required this.componentId,
    required this.verdict,
    required this.signs,
    required this.confidence,
    required this.latencyMs,
    required this.grounded,
    required this.cached,
    this.comment,
    this.surface,
    this.model,
    this.error,
    this.checkedAt,
  });

  final String componentId;

  /// `defect` | `no_defect` | `insufficient`.
  final String verdict;
  final VisionSigns signs;
  final double confidence;
  final String? comment;
  final String? surface;
  final String? model;
  final int latencyMs;
  final bool grounded;
  final String? error;
  final bool cached;
  final String? checkedAt;

  factory VisionCheck.fromJson(Map<String, dynamic> json) {
    final rawSigns = json['signs'];
    return VisionCheck(
      componentId: asString(json['component_id']) ?? '',
      verdict: asString(json['verdict']) ?? 'insufficient',
      signs: rawSigns is Map
          ? VisionSigns.fromJson(asMap(rawSigns))
          : VisionSigns.unknown,
      confidence: asDouble(json['confidence']) ?? 0,
      comment: asString(json['comment']),
      surface: asString(json['surface']),
      model: asString(json['model']),
      latencyMs: asInt(json['latency_ms']) ?? 0,
      grounded: asBool(json['grounded']) ?? false,
      error: asString(json['error']),
      cached: asBool(json['cached']) ?? false,
      checkedAt: asString(json['checked_at']),
    );
  }

  bool get hasError => error != null && error!.isNotEmpty;
  bool get isDefect => verdict == 'defect';
  bool get hasDefectSigns =>
      signs.corrosion == true ||
      signs.paintPeeling == true ||
      signs.oilLeak == true ||
      signs.mechanicalDamage == true;
}

/// Ответ `GET /vision/explain`.
class VisionExplain {
  const VisionExplain({
    required this.summary,
    required this.severity,
    required this.actions,
    required this.factsUsed,
    required this.latencyMs,
    this.model,
    this.error,
  });

  final String summary;

  /// `info` | `warning` | `critical`.
  final String severity;
  final List<String> actions;
  final List<String> factsUsed;
  final String? model;
  final int latencyMs;
  final String? error;

  factory VisionExplain.fromJson(Map<String, dynamic> json) {
    return VisionExplain(
      summary: asString(json['summary']) ?? '',
      severity: asString(json['severity']) ?? 'info',
      actions: asStringList(json['actions']),
      factsUsed: asStringList(json['facts_used']),
      model: asString(json['model']),
      latencyMs: asInt(json['latency_ms']) ?? 0,
      error: asString(json['error']),
    );
  }

  bool get hasError => error != null && error!.isNotEmpty;
  bool get isEmpty => summary.isEmpty && actions.isEmpty;
}
