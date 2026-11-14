import 'component_info.dart';
import 'json_utils.dart';

/// Карточка агрегата: `/equipment` и `/equipment/{id}`.
class Equipment {
  const Equipment({
    required this.id,
    required this.name,
    required this.components,
    this.model,
    this.serial,
    this.year,
    this.runtimeHours,
    this.normativeLifeYears,
    this.lastServiceDate,
    this.qrCode,
    this.location,
    this.baseline = const <String, dynamic>{},
    this.componentsCount,
  });

  final String id;
  final String name;
  final String? model;
  final String? serial;
  final int? year;
  final double? runtimeHours;
  final double? normativeLifeYears;
  final String? lastServiceDate;
  final String? qrCode;
  final String? location;
  final Map<String, dynamic> baseline;
  final int? componentsCount;
  final List<ComponentInfo> components;

  factory Equipment.fromJson(Map<String, dynamic> json) {
    final rawComponents = json['components'];
    return Equipment(
      id: asString(json['id']) ?? '',
      name: asString(json['name']) ?? 'Без названия',
      model: asString(json['model']),
      serial: asString(json['serial']),
      year: asInt(json['year']),
      runtimeHours: asDouble(json['runtime_hours']),
      normativeLifeYears: asDouble(json['normative_life_years']),
      lastServiceDate: asString(json['last_service_date']),
      qrCode: asString(json['qr_code']),
      location: asString(json['location']),
      baseline: asMap(json['baseline']),
      componentsCount: asInt(json['components_count']),
      components: rawComponents is List
          ? rawComponents
              .whereType<Map>()
              .map((item) => ComponentInfo.fromJson(asMap(item)))
              .toList(growable: false)
          : const <ComponentInfo>[],
    );
  }

  /// Худшее состояние среди узлов — для крупного индикатора в карточке.
  String get worstCondition {
    const order = ['possible_defect', 'check_required', 'insufficient_data', 'ok'];
    for (final status in order) {
      if (components.any((component) => component.condition == status)) {
        return status;
      }
    }
    return 'ok';
  }

  int countByCondition(String condition) =>
      components.where((component) => component.condition == condition).length;
}
