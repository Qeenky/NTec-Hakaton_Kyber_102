import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/telemetry.dart';
import 'frame_socket.dart' show LinkState;

/// Канал `/ws/telemetry`: живые значения для нижней панели AR-экрана.
class TelemetrySocket extends ChangeNotifier {
  TelemetrySocket({
    required this.baseUrl,
    required this.equipmentId,
    this.reconnectDelay = const Duration(milliseconds: 2000),
  });

  final String baseUrl;
  final String equipmentId;
  final Duration reconnectDelay;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _reconnectTimer;
  Timer? _watchdog;
  bool _disposed = false;

  LinkState state = LinkState.connecting;
  TelemetryMessage? latest;
  int received = 0;

  void connect() {
    if (_disposed) return;
    _cleanup();
    state = LinkState.connecting;
    _notify();
    _open();
  }

  Future<void> _open() async {
    if (_disposed) return;
    try {
      final uri = Uri.parse(
        '$baseUrl/ws/telemetry?equipment_id=${Uri.encodeComponent(equipmentId)}',
      );
      final channel = WebSocketChannel.connect(uri);
      await channel.ready.timeout(const Duration(seconds: 4));
      if (_disposed) {
        await channel.sink.close();
        return;
      }
      _channel = channel;
      _subscription = channel.stream.listen(
        _onMessage,
        onDone: () {
          _channel = null;
          _watchdog?.cancel();
          if (_disposed) return;
          state = LinkState.disconnected;
          _notify();
          _scheduleReconnect();
        },
        onError: (Object error) {
          _channel = null;
          _watchdog?.cancel();
          if (_disposed) return;
          state = LinkState.disconnected;
          _notify();
          _scheduleReconnect();
        },
        cancelOnError: false,
      );
      _watchdog?.cancel();
      _watchdog = Timer(const Duration(seconds: 5), () {
        if (_disposed || _channel == null) return;
        state = LinkState.noResponse;
        _notify();
      });
    } catch (_) {
      _channel = null;
      if (_disposed) return;
      state = LinkState.disconnected;
      _notify();
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic message) {
    if (_disposed || message is! String) return;
    try {
      final decoded = jsonDecode(message);
      if (decoded is! Map) return;
      latest = TelemetryMessage.fromJson(
        decoded.map((key, value) => MapEntry(key.toString(), value)),
      );
      received += 1;
      _watchdog?.cancel();
      _watchdog = Timer(const Duration(seconds: 5), () {
        if (_disposed || _channel == null) return;
        state = LinkState.noResponse;
        _notify();
      });
      state = LinkState.live;
      _notify();
    } catch (_) {
      // игнорируем битые сообщения
    }
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    state = LinkState.reconnecting;
    _notify();
    _reconnectTimer = Timer(reconnectDelay, connect);
  }

  void _cleanup() {
    _reconnectTimer?.cancel();
    _watchdog?.cancel();
    _subscription?.cancel();
    _subscription = null;
    _channel = null;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _cleanup();
    super.dispose();
  }
}
