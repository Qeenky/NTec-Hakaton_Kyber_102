import 'json_utils.dart';

/// Ответ `GET /health`.
class HealthInfo {
  const HealthInfo({
    required this.status,
    this.version,
    this.detector,
    this.detectorMode,
    this.database,
    this.rulesCount,
    this.equipmentCount,
    this.pipelineEnabled = false,
    this.pipelineLayout,
    this.error,
  });

  final String status;
  final String? version;
  final String? detector;
  final String? detectorMode;
  final String? database;
  final int? rulesCount;
  final int? equipmentCount;
  final bool pipelineEnabled;
  final String? pipelineLayout;
  final String? error;

  factory HealthInfo.fromJson(Map<String, dynamic> json) {
    final pipeline = asMap(json['pipeline']);
    return HealthInfo(
      status: asString(json['status']) ?? 'ok',
      version: asString(json['version']),
      detector: asString(json['detector']),
      detectorMode: asString(json['detector_mode']),
      database: asString(json['database']),
      rulesCount: asInt(json['rules_count']),
      equipmentCount: asInt(json['equipment_count']),
      pipelineEnabled: asBool(pipeline['enabled']) ?? false,
      pipelineLayout: asString(pipeline['layout']),
    );
  }

  String get summary {
    final parts = <String>[];
    if (detector != null) parts.add(detector!);
    if (database != null) parts.add('БД: $database');
    if (rulesCount != null) parts.add('правил: $rulesCount');
    if (equipmentCount != null) parts.add('агрегатов: $equipmentCount');
    return parts.join(' · ');
  }
}
