import 'json_utils.dart';

/// Оценка остаточного ресурса комплектующей (`remaining_resource`).
class RemainingResource {
  const RemainingResource({
    required this.valueYears,
    required this.status,
    required this.isEstimate,
    required this.basis,
  });

  final double? valueYears;
  final String status;
  final bool isEstimate;
  final Map<String, dynamic> basis;

  factory RemainingResource.fromJson(Map<String, dynamic> json) {
    return RemainingResource(
      valueYears: asDouble(json['value']),
      status: asString(json['status']) ?? 'insufficient_data',
      isEstimate: asBool(json['is_estimate']) ?? true,
      basis: asMap(json['basis']),
    );
  }

  String get text {
    final value = valueYears;
    if (value == null) return 'н/д';
    final suffix = isEstimate ? ' · оценка' : '';
    return '≈ ${value.toStringAsFixed(1)} лет$suffix';
  }
}

/// Комплектующая из карточки `/equipment/{id}`.
class ComponentInfo {
  const ComponentInfo({
    required this.id,
    required this.name,
    required this.kind,
    required this.condition,
    this.normativeLifeYears,
    this.installedDate,
    this.remainingResource,
  });

  final int id;
  final String name;
  final String kind;
  final String condition;
  final double? normativeLifeYears;
  final String? installedDate;
  final RemainingResource? remainingResource;

  factory ComponentInfo.fromJson(Map<String, dynamic> json) {
    final resource = json['remaining_resource'];
    return ComponentInfo(
      id: asInt(json['id']) ?? 0,
      name: asString(json['name']) ?? '',
      kind: asString(json['kind']) ?? 'component',
      condition: asString(json['condition']) ?? 'insufficient_data',
      normativeLifeYears: asDouble(json['normative_life_years']),
      installedDate: asString(json['installed_date']),
      remainingResource: resource is Map
          ? RemainingResource.fromJson(asMap(resource))
          : null,
    );
  }
}
