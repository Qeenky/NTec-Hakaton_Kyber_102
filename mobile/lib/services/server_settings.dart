/// Адрес сервера: хранится в shared_preferences как «host:port».
class ServerSettings {
  const ServerSettings({required this.host, required this.port});

  final String host;
  final int port;

  static const String defaultAddress = '192.168.137.1:8000';
  static const String usbAddress = '127.0.0.1:8000';
  static const ServerSettings defaults =
      ServerSettings(host: '192.168.137.1', port: 8000);

  String get authority => '$host:$port';
  String get baseUrl => 'http://$authority';
  String get wsBaseUrl => 'ws://$authority';

  ServerSettings copyWith({String? host, int? port}) =>
      ServerSettings(host: host ?? this.host, port: port ?? this.port);

  /// Разбирает `192.168.137.1:8000`, `http://host:8000/`, `host`.
  /// Возвращает null, если адрес совсем некорректный.
  static ServerSettings? tryParse(String raw) {
    var value = raw.trim();
    if (value.isEmpty) return null;
    final lower = value.toLowerCase();
    for (final scheme in const ['http://', 'https://', 'ws://', 'wss://']) {
      if (lower.startsWith(scheme)) {
        value = value.substring(scheme.length);
        break;
      }
    }
    final slash = value.indexOf('/');
    if (slash >= 0) value = value.substring(0, slash);
    if (value.isEmpty) return null;

    var host = value;
    var portText = '8000';
    final colon = value.lastIndexOf(':');
    if (colon > 0) {
      host = value.substring(0, colon);
      portText = value.substring(colon + 1);
    }
    if (host.isEmpty || host.contains(' ')) return null;
    if (!RegExp(r'^[A-Za-z0-9._-]+$').hasMatch(host)) return null;
    final port = int.tryParse(portText);
    if (port == null || port < 1 || port > 65535) return null;
    return ServerSettings(host: host, port: port);
  }

  static ServerSettings parse(String raw) => tryParse(raw) ?? defaults;

  @override
  String toString() => authority;

  @override
  bool operator ==(Object other) =>
      other is ServerSettings && other.host == host && other.port == port;

  @override
  int get hashCode => Object.hash(host, port);
}
