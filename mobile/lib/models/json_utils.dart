/// Мягкие парсеры JSON: сервер может прислать null, строку или число.
library;

double? asDouble(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value.replaceAll(',', '.'));
  return null;
}

int? asInt(Object? value) {
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value);
  return null;
}

String? asString(Object? value) {
  if (value == null) return null;
  if (value is String) return value;
  return value.toString();
}

bool? asBool(Object? value) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) {
    final normalized = value.toLowerCase();
    if (normalized == 'true' || normalized == '1') return true;
    if (normalized == 'false' || normalized == '0') return false;
  }
  return null;
}

Map<String, dynamic> asMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((key, item) => MapEntry(key.toString(), item));
  }
  return const <String, dynamic>{};
}

List<Map<String, dynamic>> asMapList(Object? value) {
  if (value is! List) return const <Map<String, dynamic>>[];
  return value.whereType<Map>().map((item) => asMap(item)).toList();
}

List<String> asStringList(Object? value) {
  if (value is! List) return const <String>[];
  return value
      .map((item) => asString(item))
      .whereType<String>()
      .where((item) => item.isNotEmpty)
      .toList(growable: false);
}

List<List<double>> asPolygon(Object? value) {
  if (value is! List) return const <List<double>>[];
  final points = <List<double>>[];
  for (final point in value) {
    if (point is List && point.length >= 2) {
      final x = asDouble(point[0]);
      final y = asDouble(point[1]);
      if (x != null && y != null) points.add(<double>[x, y]);
    }
  }
  return points;
}

List<double> asBbox(Object? value) {
  if (value is! List || value.length < 4) return const <double>[];
  return value
      .take(4)
      .map((item) => asDouble(item) ?? 0)
      .toList(growable: false);
}
