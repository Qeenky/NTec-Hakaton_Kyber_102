import 'json_utils.dart';
import 'labels.dart';

/// Визуальный признак, который отслеживается по окну наблюдения.
class VisualSign {
  const VisualSign({
    required this.sign,
    required this.label,
    required this.status,
    required this.sources,
    required this.confidence,
    required this.presenceRatio,
    required this.framesPresent,
    this.firstSeenAgoSec,
    this.lastSeenAgoSec,
    this.trend,
  });

  final String sign;
  final String label;

  /// `detected` | `single_observation` | `not_detected` | `not_visible`.
  final String status;
  final List<String> sources;
  final double confidence;
  final double presenceRatio;
  final int framesPresent;
  final double? firstSeenAgoSec;
  final double? lastSeenAgoSec;
  final String? trend;

  factory VisualSign.fromJson(Map<String, dynamic> json) {
    final sign = asString(json['sign']) ?? '';
    final confidence = asDouble(json['confidence']) ?? 0;
    final presence = asDouble(json['presence_ratio']) ?? 0;
    var status = asString(json['status']) ?? '';
    if (status.isEmpty) {
      status = confidence > 0 || presence > 0
          ? 'single_observation'
          : 'not_detected';
    }
    final label = asString(json['label']);
    return VisualSign(
      sign: sign,
      label: label == null || label.isEmpty
          ? visualSignDefaultLabel(sign)
          : label,
      status: status,
      sources: asStringList(json['sources']),
      confidence: confidence,
      presenceRatio: presence,
      framesPresent: asInt(json['frames_present']) ?? 0,
      firstSeenAgoSec: asDouble(json['first_seen_ago_sec']),
      lastSeenAgoSec: asDouble(json['last_seen_ago_sec']),
      trend: asString(json['trend']),
    );
  }

  bool get isDetected => status == 'detected';
  bool get isSingle => status == 'single_observation';
  bool get isNotVisible => status == 'not_visible';
  bool get isNotDetected => status == 'not_detected';

  /// Показываем только подтверждённые и единичные наблюдения.
  bool get isShown => isDetected || isSingle;
}

/// Русские названия признаков, если сервер не прислал `label`.
String visualSignDefaultLabel(String sign) {
  final key = sign.toLowerCase().trim();
  const labels = <String, String>{
    'corrosion': 'Коррозия',
    'oil_leak': 'Подтёк масла',
    'oxidation': 'Окисление контактов',
    'contact_oxidation': 'Окисление контактов',
    'electrical_oxidation': 'Окисление контактов',
    'paint_peeling': 'Отслоение краски',
    'mechanical_damage': 'Механическое повреждение',
    'damage': 'Механическое повреждение',
    'crack': 'Трещина/скол',
    'chip': 'Трещина/скол',
    'crack_chip': 'Трещина/скол',
    'guard_missing': 'Отсутствие кожуха',
    'missing_guard': 'Отсутствие кожуха',
    'guard_removed': 'Отсутствие кожуха',
    'guard_damage': 'Повреждение кожуха',
  };
  if (labels.containsKey(key)) return labels[key]!;
  if (key.isEmpty) return 'Признак';
  final text = key.replaceAll('_', ' ');
  return text[0].toUpperCase() + text.substring(1);
}

/// Дефект из окна видеоаналитики.
class AnalyticsDefect {
  const AnalyticsDefect({
    required this.className,
    required this.presenceRatio,
    required this.meanConfidence,
    required this.confirmed,
  });

  final String className;
  final double presenceRatio;
  final double meanConfidence;
  final bool confirmed;

  factory AnalyticsDefect.fromJson(
    Map<String, dynamic> json, {
    bool defaultConfirmed = false,
  }) {
    return AnalyticsDefect(
      className: asString(json['class_name']) ?? '',
      presenceRatio: asDouble(json['presence_ratio']) ?? 0,
      meanConfidence: asDouble(json['mean_confidence']) ?? 0,
      confirmed: asBool(json['confirmed']) ?? defaultConfirmed,
    );
  }

  String get label => componentLabel(className).toLowerCase();
}

/// Оценка одного узла по окну наблюдения.
class AnalyticsComponent {
  const AnalyticsComponent({
    required this.componentId,
    required this.label,
    required this.visibilityRatio,
    required this.framesSeen,
    required this.framesTotal,
    required this.statusWindow,
    required this.confidence,
    required this.defects,
    this.signs = const <VisualSign>[],
    this.lastSeenAgoSec,
    this.note,
  });

  final String componentId;
  final String label;
  final double visibilityRatio;
  final int framesSeen;
  final int framesTotal;

  /// `ok` | `check_required` | `possible_defect` | `insufficient_data`.
  final String statusWindow;
  final double confidence;
  final List<AnalyticsDefect> defects;
  final List<VisualSign> signs;
  final double? lastSeenAgoSec;
  final String? note;

  factory AnalyticsComponent.fromJson(
    Map<String, dynamic> json, {
    int defaultFramesTotal = 0,
  }) {
    final framesTotal =
        asInt(json['frames_total']) ?? asInt(json['frames']) ?? defaultFramesTotal;
    final framesSeen = asInt(json['frames_seen']) ??
        asInt(json['frames_present']) ??
        0;

    final rawDefects = json['defects'];
    final defects = <AnalyticsDefect>[];
    if (rawDefects is List) {
      for (final item in rawDefects) {
        if (item is Map) {
          defects.add(
            AnalyticsDefect.fromJson(
              asMap(item),
              defaultConfirmed: true,
            ),
          );
        }
      }
    } else {
      final rawConfirmed = json['confirmed_defects'];
      if (rawConfirmed is List) {
        for (final item in rawConfirmed) {
          if (item is Map) {
            defects.add(
              AnalyticsDefect.fromJson(
                asMap(item),
                defaultConfirmed: true,
              ),
            );
          }
        }
      }
    }

    var ratio = asDouble(json['visibility_ratio']) ??
        asDouble(json['visible_ratio']);
    ratio ??= framesTotal > 0 ? framesSeen / framesTotal : 0.0;

    final componentId = asString(json['component_id']) ?? '';
    final rawSigns = json['signs'];
    final signs = <VisualSign>[];
    if (rawSigns is List) {
      for (final item in rawSigns) {
        if (item is Map) signs.add(VisualSign.fromJson(asMap(item)));
      }
    }
    return AnalyticsComponent(
      componentId: componentId,
      label: asString(json['label']) ?? componentLabel(componentId),
      visibilityRatio: ratio.clamp(0.0, 1.0).toDouble(),
      framesSeen: framesSeen,
      framesTotal: framesTotal,
      statusWindow: asString(json['status_window']) ??
          _deriveStatus(framesSeen, defects),
      confidence: asDouble(json['confidence']) ?? 0,
      defects: defects,
      signs: signs,
      lastSeenAgoSec: asDouble(json['last_seen_ago_sec']),
      note: asString(json['note']),
    );
  }

  factory AnalyticsComponent.missing(String componentId) {
    return AnalyticsComponent(
      componentId: componentId,
      label: componentLabel(componentId),
      visibilityRatio: 0,
      framesSeen: 0,
      framesTotal: 0,
      statusWindow: 'insufficient_data',
      confidence: 0,
      defects: const <AnalyticsDefect>[],
      signs: const <VisualSign>[],
      note: 'не было в окне наблюдения',
    );
  }

  static String _deriveStatus(int framesSeen, List<AnalyticsDefect> defects) {
    if (framesSeen <= 0) return 'insufficient_data';
    if (defects.any((item) => item.confirmed)) return 'possible_defect';
    return 'ok';
  }

  bool get outOfFrame => framesSeen <= 0 || visibilityRatio <= 0;

  List<AnalyticsDefect> get confirmedDefects =>
      defects.where((item) => item.confirmed).toList(growable: false);

  int get sortPriority {
    if (outOfFrame) return 3;
    switch (statusWindow) {
      case 'possible_defect':
        return 0;
      case 'check_required':
        return 1;
      case 'ok':
        return 2;
      default:
        return 3;
    }
  }
}

/// Блок `analytics` из `/ws/frame`, `/analyze` или `/inspections`.
class FrameAnalytics {
  const FrameAnalytics({
    required this.windowSec,
    required this.frames,
    required this.durationSec,
    required this.components,
  });

  final double windowSec;
  final int frames;
  final double durationSec;
  final List<AnalyticsComponent> components;

  factory FrameAnalytics.fromJson(Map<String, dynamic> json) {
    final frames = asInt(json['frames']) ?? 0;
    final components = <AnalyticsComponent>[];
    final raw = json['components'];
    if (raw is List) {
      for (final item in raw) {
        if (item is Map) {
          components.add(
            AnalyticsComponent.fromJson(
              asMap(item),
              defaultFramesTotal: frames,
            ),
          );
        }
      }
    }
    return FrameAnalytics(
      windowSec: asDouble(json['window_sec']) ?? 60,
      frames: frames,
      durationSec: asDouble(json['duration_sec']) ?? 0,
      components: components,
    );
  }

  static FrameAnalytics? fromJsonOrNull(Object? raw) {
    if (raw is! Map) return null;
    final analytics = FrameAnalytics.fromJson(asMap(raw));
    if (analytics.frames <= 0 && analytics.components.isEmpty) return null;
    return analytics;
  }

  bool get isEmpty => frames <= 0 && components.isEmpty;

  /// Все узлы состава, включая отсутствовавшие в окне (серые «не в кадре»).
  List<AnalyticsComponent> withRoster(Iterable<String> roster) {
    final result = <AnalyticsComponent>[...components];
    final present = components
        .map((component) => component.componentId)
        .where((id) => id.isNotEmpty)
        .toSet();
    for (final id in roster) {
      if (id.isEmpty || present.contains(id)) continue;
      result.add(AnalyticsComponent.missing(id));
    }
    result.sort((a, b) {
      final byPriority = a.sortPriority.compareTo(b.sortPriority);
      if (byPriority != 0) return byPriority;
      return a.label.compareTo(b.label);
    });
    return result;
  }

  String get headerText =>
      'Наблюдение · ${durationSec.toStringAsFixed(1)} с · $frames кадров';

  String signature() {
    final parts = components.map(
      (component) => '${component.componentId}:${component.framesSeen}:'
          '${component.statusWindow}:${component.visibilityRatio}:'
          '${component.confirmedDefects.length}:'
          '${component.signs.map((sign) => '${sign.sign}:${sign.status}:${sign.confidence}:${sign.framesPresent}').join('+')}',
    );
    return '$frames|$durationSec|${parts.join(',')}';
  }
}
