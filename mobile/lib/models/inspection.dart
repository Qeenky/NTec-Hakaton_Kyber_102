import 'analytics.dart';
import 'component_info.dart';
import 'diagnostic.dart';
import 'frame_result.dart';
import 'json_utils.dart';
import 'labels.dart';
import 'verdict.dart';

/// Снимок осмотра из `GET /inspections`.
class InspectionRecord {
  const InspectionRecord({
    required this.id,
    required this.equipmentId,
    required this.createdAt,
    required this.summary,
    required this.diagnostics,
    this.analytics,
    this.verdict,
  });

  final int id;
  final String equipmentId;
  final String? createdAt;
  final InspectionSummary summary;
  final List<DiagnosticEvent> diagnostics;
  final FrameAnalytics? analytics;
  final InspectionVerdict? verdict;

  factory InspectionRecord.fromJson(Map<String, dynamic> json) {
    final rawSummary = asMap(json['summary']);
    final summary = InspectionSummary.fromJson(rawSummary);
    return InspectionRecord(
      id: asInt(json['id']) ?? 0,
      equipmentId: asString(json['equipment_id']) ?? '',
      createdAt: asString(json['created_at']),
      summary: summary,
      diagnostics: asMapList(json['diagnostics'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      analytics:
          FrameAnalytics.fromJsonOrNull(json['analytics']) ?? summary.analytics,
      verdict: InspectionVerdict.fromJsonOrNull(json['verdict']) ??
          InspectionVerdict.fromJsonOrNull(rawSummary['verdict']),
    );
  }

  /// Серверный вердикт или fallback по сохранённым статусам и аналитике.
  InspectionVerdict effectiveVerdict({List<String> roster = const <String>[]}) {
    final server = verdict;
    if (server != null) return server;

    final observations = <VerdictObservation>[];
    for (final item in summary.componentsStatus) {
      observations.add(
        VerdictObservation(
          componentId: item.componentId,
          status: item.status,
          seen: item.status != 'insufficient_data',
        ),
      );
    }
    final window = analytics;
    if (window != null) {
      for (final component in window.components) {
        observations.add(
          VerdictObservation(
            componentId: component.componentId,
            status: component.confirmedDefects.isNotEmpty
                ? 'possible_defect'
                : component.statusWindow,
            seen: !component.outOfFrame,
          ),
        );
      }
    }
    final hasObservation =
        summary.componentsStatus.isNotEmpty || (window?.components.isNotEmpty ?? false);
    return InspectionVerdict.resolve(
      roster: roster,
      observations: observations,
      hasFrame: hasObservation,
      hasDefectEvidence: summary.defectCount > 0,
    );
  }

  DateTime? get parsedAt =>
      createdAt == null ? null : DateTime.tryParse(createdAt!);
}

/// `summary` сохранённого осмотра.
class InspectionSummary {
  const InspectionSummary({
    required this.frameId,
    required this.imageWidth,
    required this.imageHeight,
    required this.inferenceMs,
    required this.detections,
    required this.componentsStatus,
    required this.diagnostics,
    this.analytics,
  });

  final int? frameId;
  final int? imageWidth;
  final int? imageHeight;
  final int? inferenceMs;
  final List<FrameDetection> detections;
  final List<FrameComponentStatus> componentsStatus;
  final List<DiagnosticEvent> diagnostics;
  final FrameAnalytics? analytics;

  factory InspectionSummary.fromJson(Map<String, dynamic> json) {
    return InspectionSummary(
      frameId: asInt(json['frame_id']),
      imageWidth: asInt(json['image_width']),
      imageHeight: asInt(json['image_height']),
      inferenceMs: asInt(json['inference_ms']),
      detections: asMapList(json['detections'])
          .map(FrameDetection.fromJson)
          .toList(growable: false),
      componentsStatus: asMapList(json['components_status'])
          .map(FrameComponentStatus.fromJson)
          .toList(growable: false),
      diagnostics: asMapList(json['diagnostics'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      analytics: FrameAnalytics.fromJsonOrNull(json['analytics']),
    );
  }

  static const empty = InspectionSummary(
    frameId: null,
    imageWidth: null,
    imageHeight: null,
    inferenceMs: null,
    detections: <FrameDetection>[],
    componentsStatus: <FrameComponentStatus>[],
    diagnostics: <DiagnosticEvent>[],
  );

  int get defectCount =>
      detections.where((detection) => detection.isDefect).length;

  int get problemZones => componentsStatus
      .where((item) =>
          item.status == 'possible_defect' || item.status == 'check_required')
      .length;

  String get shortStatus {
    if (defectCount > 0) return 'дефектов: $defectCount';
    if (problemZones > 0) return 'зон к проверке: $problemZones';
    return 'без замечаний';
  }
}

/// Ответ `POST /inspections`.
class SavedInspection {
  const SavedInspection({
    required this.id,
    required this.equipmentId,
    required this.createdAt,
    required this.componentsStatus,
    required this.diagnostics,
    this.analytics,
    this.verdict,
  });

  final int id;
  final String equipmentId;
  final String? createdAt;
  final List<FrameComponentStatus> componentsStatus;
  final List<DiagnosticEvent> diagnostics;
  final FrameAnalytics? analytics;
  final InspectionVerdict? verdict;

  factory SavedInspection.fromJson(Map<String, dynamic> json) {
    return SavedInspection(
      id: asInt(json['id']) ?? 0,
      equipmentId: asString(json['equipment_id']) ?? '',
      createdAt: asString(json['created_at']),
      componentsStatus: asMapList(json['components_status'])
          .map(FrameComponentStatus.fromJson)
          .toList(growable: false),
      diagnostics: asMapList(json['diagnostics'])
          .map(DiagnosticEvent.fromJson)
          .toList(growable: false),
      analytics: FrameAnalytics.fromJsonOrNull(json['analytics']),
      verdict: InspectionVerdict.fromJsonOrNull(json['verdict']),
    );
  }

  /// Подпись обновившихся узлов — показываем после сохранения.
  String get updatedComponentsText {
    final changed = componentsStatus
        .where((item) => item.status != 'insufficient_data')
        .map((item) =>
            '${componentLabel(item.componentId)} — ${componentStatusLabel(item.status)}')
        .toList(growable: false);
    return changed.isEmpty ? 'Состав узлов не изменился' : changed.join('\n');
  }
}

/// Узел для итогового экрана: подпись + статус + источник.
class ResultZone {
  const ResultZone({
    required this.id,
    required this.label,
    required this.status,
    required this.color,
    this.visible = true,
    this.defects = const <String>[],
    this.detail,
  });

  final String id;
  final String label;
  final String status;
  final String color;
  final bool visible;
  final List<String> defects;
  final String? detail;

  factory ResultZone.fromOverlay(OverlayZone zone) {
    return ResultZone(
      id: zone.componentId,
      label: zone.label.isNotEmpty ? zone.label : componentLabel(zone.componentId),
      status: zone.status,
      color: zone.color,
      visible: zone.visible && zone.hasGeometry,
      defects: zone.defects,
    );
  }

  factory ResultZone.fromStatus(FrameComponentStatus status) {
    return ResultZone(
      id: status.componentId,
      label: componentLabel(status.componentId),
      status: status.status,
      color: _colorForStatus(status.status),
      defects: status.defects,
    );
  }

  factory ResultZone.fromComponent(ComponentInfo component) {
    return ResultZone(
      id: component.name,
      label: componentLabel(component.name),
      status: component.condition,
      color: _colorForStatus(component.condition),
      detail: component.remainingResource?.text,
    );
  }

  static String _colorForStatus(String status) {
    switch (status) {
      case 'ok':
        return 'green';
      case 'check_required':
        return 'yellow';
      case 'possible_defect':
        return 'red';
      default:
        return 'grey';
    }
  }
}
