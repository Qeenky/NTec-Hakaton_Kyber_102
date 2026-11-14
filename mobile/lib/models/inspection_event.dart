import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:intl/intl.dart';

/// Событие журнала осмотра.
class InspectionEvent {
  const InspectionEvent({
    required this.timestamp,
    required this.type,
    required this.message,
    this.frameId,
    this.severity = 'info',
  });

  final DateTime timestamp;
  final int? frameId;
  final String type;
  final String message;

  /// `info` | `warning` | `critical`.
  final String severity;

  factory InspectionEvent.fromJson(Map<String, dynamic> json) {
    final rawTimestamp = json['timestamp']?.toString();
    return InspectionEvent(
      timestamp:
          rawTimestamp == null ? DateTime.now() : (DateTime.tryParse(rawTimestamp) ?? DateTime.now()),
      frameId: json['frame_id'] is num ? (json['frame_id'] as num).toInt() : null,
      type: json['type']?.toString() ?? 'info',
      message: json['message']?.toString() ?? '',
      severity: json['severity']?.toString() ?? 'info',
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'timestamp': timestamp.toUtc().toIso8601String(),
        'frame_id': frameId,
        'type': type,
        'message': message,
        'severity': severity,
      };

  String get timeLabel => DateFormat('HH:mm:ss').format(timestamp.toLocal());
}

String eventTypeLabel(String type) {
  switch (type) {
    case 'binding_lost':
      return 'Привязка потеряна';
    case 'binding_restored':
      return 'Привязка восстановлена';
    case 'board_changed':
      return 'Смена планшета';
    case 'sign_appeared':
      return 'Появился признак';
    case 'sign_disappeared':
      return 'Признак исчез';
    case 'diagnostic_triggered':
      return 'Диагностика';
    case 'diagnostic_cleared':
      return 'Диагностика снята';
    case 'link_lost':
      return 'Связь потеряна';
    case 'link_restored':
      return 'Связь восстановлена';
    case 'ai_check':
      return 'AI-проверка';
    case 'photo_analyzed':
      return 'Фото обработано';
    case 'error':
      return 'Ошибка';
    default:
      return 'Событие';
  }
}

/// Журнал в памяти на время осмотра: новые сверху, максимум 500 записей.
/// Уведомления батчатся не чаще 2 раз в секунду.
class InspectionEventLog extends ChangeNotifier {
  static const int maxEvents = 500;
  static const Duration _notifyPeriod = Duration(milliseconds: 500);

  final List<InspectionEvent> _events = <InspectionEvent>[];
  Timer? _timer;
  bool _dirty = false;

  List<InspectionEvent> get events =>
      List<InspectionEvent>.unmodifiable(_events);
  int get length => _events.length;
  bool get isEmpty => _events.isEmpty;

  void add(InspectionEvent event) {
    _events.insert(0, event);
    if (_events.length > maxEvents) {
      _events.removeRange(maxEvents, _events.length);
    }
    _dirty = true;
    _timer ??= Timer(_notifyPeriod, _flush);
  }

  void _flush() {
    _timer = null;
    if (_dirty) {
      _dirty = false;
      notifyListeners();
    }
  }

  /// Немедленно отдать накопленные изменения (перед открытием шторки/сохранением).
  void flushNow() {
    _timer?.cancel();
    _timer = null;
    if (_dirty) {
      _dirty = false;
      notifyListeners();
    }
  }

  List<Map<String, dynamic>> toJson() =>
      _events.map((event) => event.toJson()).toList(growable: false);

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}
