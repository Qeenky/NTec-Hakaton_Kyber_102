import 'diagnostic.dart';
import 'json_utils.dart';

/// Один сэмпл телеметрии (1 Гц).
class TelemetrySample {
  const TelemetrySample({
    required this.ts,
    required this.scenario,
    required this.signals,
  });

  final DateTime? ts;
  final String? scenario;
  final Map<String, dynamic> signals;

  factory TelemetrySample.fromJson(Map<String, dynamic> json) {
    final rawTs = asString(json['ts']);
    return TelemetrySample(
      ts: rawTs == null ? null : DateTime.tryParse(rawTs),
      scenario: asString(json['scenario']),
      signals: asMap(json['signals']),
    );
  }

  double? value(String key) => asDouble(signals[key]);
}

/// Ответ `GET /equipment/{id}/telemetry?minutes=5`.
class TelemetryHistory {
  const TelemetryHistory({
    required this.equipmentId,
    required this.minutes,
    required this.samples,
    this.scenario,
    this.latest,
  });

  final String equipmentId;
  final int minutes;
  final String? scenario;
  final TelemetrySample? latest;
  final List<TelemetrySample> samples;

  factory TelemetryHistory.fromJson(Map<String, dynamic> json) {
    final rawLatest = json['latest'];
    return TelemetryHistory(
      equipmentId: asString(json['equipment_id']) ?? '',
      minutes: asInt(json['minutes']) ?? 5,
      scenario: asString(json['scenario']),
      latest: rawLatest is Map ? TelemetrySample.fromJson(asMap(rawLatest)) : null,
      samples: asMapList(json['samples'])
          .map(TelemetrySample.fromJson)
          .toList(growable: false),
    );
  }

  static const empty = TelemetryHistory(
    equipmentId: '',
    minutes: 5,
    samples: <TelemetrySample>[],
  );

  /// Пары (индекс, значение) для мини-графика.
  List<double> series(String key) {
    final values = <double>[];
    for (final sample in samples) {
      final value = sample.value(key);
      if (value != null) values.add(value);
    }
    return values;
  }

  Object? latestSignal(String key) => latest?.signals[key];

  double? minMaxDelta(String key) {
    final values = series(key);
    if (values.isEmpty) return null;
    final min = values.reduce((a, b) => a < b ? a : b);
    final max = values.reduce((a, b) => a > b ? a : b);
    return max - min;
  }
}

/// Сообщение `/ws/telemetry` (1 Гц).
class TelemetryMessage {
  const TelemetryMessage({
    required this.signals,
    required this.activeDiagnostics,
    required this.blockedDiagnostics,
    this.ts,
    this.equipmentId,
    this.scenario,
  });

  final DateTime? ts;
  final String? equipmentId;
  final String? scenario;
  final Map<String, dynamic> signals;
  final List<DiagnosticEvent> activeDiagnostics;
  final List<BlockedDiagnostic> blockedDiagnostics;

  factory TelemetryMessage.fromJson(Map<String, dynamic> json) {
    final rawTs = asString(json['ts']);
    return TelemetryMessage(
      ts: rawTs == null ? null : DateTime.tryParse(rawTs),
      equipmentId: asString(json['equipment_id']),
      scenario: asString(json['scenario']),
      signals: asMap(json['signals']),
      activeDiagnostics: asMapList(json['active_diagnostics'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      blockedDiagnostics: asMapList(json['blocked_diagnostics'])
          .map(BlockedDiagnostic.fromJson)
          .toList(growable: false),
    );
  }

  double? value(String key) => asDouble(signals[key]);
}
