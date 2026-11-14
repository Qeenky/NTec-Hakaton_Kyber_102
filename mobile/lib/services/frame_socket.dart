import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/frame_result.dart';

enum LinkState {
  connecting,
  live,
  noResponse,
  reconnecting,
  disconnected,
}

extension LinkStateText on LinkState {
  String get label {
    switch (this) {
      case LinkState.connecting:
        return 'Подключение';
      case LinkState.live:
        return 'В сети';
      case LinkState.noResponse:
        return 'Нет ответа';
      case LinkState.reconnecting:
        return 'Переподключение…';
      case LinkState.disconnected:
        return 'Нет связи';
    }
  }
}

/// Канал `/ws/frame`: отправляет JPEG, получает JSON с оверлеями.
///
/// Если сервер молчит дольше [responseTimeout], состояние переходит в
/// [LinkState.noResponse], но соединение и отправка продолжаются. При обрыве
/// переподключается каждые [reconnectDelay].
class FrameSocket extends ChangeNotifier {
  FrameSocket({
    required this.baseUrl,
    required this.equipmentId,
    this.responseTimeout = const Duration(seconds: 3),
    this.reconnectDelay = const Duration(seconds: 2),
  });

  final String baseUrl;
  final String equipmentId;
  final Duration responseTimeout;
  final Duration reconnectDelay;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _watchdog;
  Timer? _reconnectTimer;
  bool _disposed = false;
  bool _fatal = false;

  LinkState state = LinkState.connecting;
  FrameResult? lastFrame;
  String? fatalError;
  DateTime? lastResponseAt;
  int sentFrames = 0;
  int receivedFrames = 0;

  bool get isConnected => _channel != null && !_fatal;

  void connect() {
    if (_disposed || _fatal) return;
    _cleanupSocket();
    state = LinkState.connecting;
    _notify();
    _open();
  }

  Future<void> _open() async {
    if (_disposed || _fatal) return;
    try {
      final uri = Uri.parse(
        '$baseUrl/ws/frame?equipment_id=${Uri.encodeComponent(equipmentId)}',
      );
      final channel = WebSocketChannel.connect(uri);
      await channel.ready.timeout(const Duration(seconds: 4));
      if (_disposed || _fatal) {
        await channel.sink.close();
        return;
      }
      _channel = channel;
      state = LinkState.live;
      _notify();
      _subscription = channel.stream.listen(
        _onMessage,
        onDone: _onDone,
        onError: (Object error) => _onError(error),
        cancelOnError: false,
      );
    } catch (_) {
      _channel = null;
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
      final frame = FrameResult.fromJson(
        decoded.map((key, value) => MapEntry(key.toString(), value)),
      );
      lastFrame = frame;
      receivedFrames += 1;
      lastResponseAt = DateTime.now();
      _watchdog?.cancel();
      state = LinkState.live;
      _notify();
    } catch (_) {
      // битый JSON не должен ронять приложение
    }
  }

  void _onDone() {
    final code = _channel?.closeCode;
    final reason = _channel?.closeReason;
    _channel = null;
    _watchdog?.cancel();
    if (_disposed) return;
    if (code == 1008) {
      _fatal = true;
      fatalError = reason == 'equipment_not_found'
          ? 'Агрегат не найден на сервере'
          : 'Сервер закрыл соединение (${reason ?? 'код 1008'})';
      state = LinkState.disconnected;
      _notify();
      return;
    }
    state = LinkState.disconnected;
    _notify();
    if (!_fatal) _scheduleReconnect();
  }

  void _onError(Object error) {
    _channel = null;
    _watchdog?.cancel();
    state = LinkState.disconnected;
    _notify();
    if (!_fatal) _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed || _fatal) return;
    _reconnectTimer?.cancel();
    state = LinkState.reconnecting;
    _notify();
    _reconnectTimer = Timer(reconnectDelay, connect);
  }

  void _cleanupSocket() {
    _watchdog?.cancel();
    _reconnectTimer?.cancel();
    _subscription?.cancel();
    _subscription = null;
    _channel = null;
  }

  /// Отправка бинарного JPEG. Если канал не готов — кадр тихо пропускается.
  void send(Uint8List jpeg) {
    final channel = _channel;
    if (_disposed || channel == null) return;
    try {
      channel.sink.add(jpeg);
      sentFrames += 1;
      _watchdog?.cancel();
      _watchdog = Timer(responseTimeout, () {
        if (_disposed) return;
        state = LinkState.noResponse;
        _notify();
      });
      _notify();
    } catch (_) {
      _onError('send failed');
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _cleanupSocket();
    _subscription?.cancel();
    _subscription = null;
    super.dispose();
  }
}
