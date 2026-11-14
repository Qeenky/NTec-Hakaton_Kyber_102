import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/health_info.dart';
import 'api_service.dart';

enum ConnectionStatus {
  unknown,
  checking,
  online,
  offline,
}

/// Периодическая проверка `GET /health` для индикатора связи в шапке.
class ConnectionMonitor extends ChangeNotifier {
  ConnectionMonitor(this._api, {this.interval = const Duration(seconds: 10)});

  ApiService _api;
  final Duration interval;
  Timer? _timer;
  bool _checking = false;
  bool _disposed = false;

  ConnectionStatus status = ConnectionStatus.unknown;
  HealthInfo? health;
  String? error;
  DateTime? checkedAt;

  void setApi(ApiService api) {
    _api = api;
  }

  void start() {
    _timer?.cancel();
    _timer = Timer.periodic(interval, (_) => check());
    check();
  }

  Future<void> check() async {
    if (_checking || _disposed) return;
    _checking = true;
    final previous = status;
    if (previous == ConnectionStatus.unknown) {
      status = ConnectionStatus.checking;
      _notify();
    }
    try {
      final info = await _api.fetchHealth();
      if (_disposed) return;
      health = info;
      error = null;
      status = ConnectionStatus.online;
      checkedAt = DateTime.now();
    } on ApiException catch (exception) {
      if (_disposed) return;
      error = exception.message;
      status = ConnectionStatus.offline;
      checkedAt = DateTime.now();
    } catch (_) {
      if (_disposed) return;
      error = 'Нет связи с сервером';
      status = ConnectionStatus.offline;
      checkedAt = DateTime.now();
    } finally {
      _checking = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    super.dispose();
  }
}
