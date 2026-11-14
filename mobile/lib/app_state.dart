import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'services/api_service.dart';
import 'services/connection_monitor.dart';
import 'services/server_settings.dart';

/// Хранилище настроек (shared_preferences).
class SettingsStore {
  SettingsStore(this._prefs);

  static const String key = 'server_address';
  static const String themeKey = 'theme_mode';
  static const String equipmentKey = 'selected_equipment_id';
  final SharedPreferences _prefs;

  ServerSettings load() =>
      ServerSettings.parse(_prefs.getString(key) ?? ServerSettings.defaultAddress);

  Future<void> save(ServerSettings settings) =>
      _prefs.setString(key, settings.authority);

  ThemeMode loadThemeMode() {
    switch (_prefs.getString(themeKey)) {
      case 'light':
        return ThemeMode.light;
      case 'dark':
        return ThemeMode.dark;
      case 'system':
      default:
        return ThemeMode.system;
    }
  }

  Future<void> saveThemeMode(ThemeMode mode) =>
      _prefs.setString(themeKey, mode.name);

  String loadSelectedEquipment() =>
      _prefs.getString(equipmentKey) ?? 'PUMP-001';

  Future<void> saveSelectedEquipment(String id) =>
      _prefs.setString(equipmentKey, id);
}

/// Глобальное состояние: настройки, тема, активный агрегат, REST-клиент, индикатор связи.
class AppState extends ChangeNotifier {
  AppState({
    required SettingsStore store,
    required ServerSettings settings,
    ApiService? api,
    ThemeMode? themeMode,
    String? selectedEquipmentId,
  }) : this._(
          store,
          settings,
          api,
          themeMode ?? store.loadThemeMode(),
          selectedEquipmentId ?? store.loadSelectedEquipment(),
        );

  AppState._(
    this._store,
    this._settings,
    ApiService? api,
    this._themeMode,
    this._selectedEquipmentId,
  ) : api = api ?? ApiService(_settings.baseUrl) {
    monitor = ConnectionMonitor(this.api);
    monitor.addListener(_onMonitorChanged);
  }

  final SettingsStore _store;
  final ApiService api;
  late final ConnectionMonitor monitor;

  ServerSettings _settings;
  ServerSettings get settings => _settings;

  ThemeMode _themeMode;
  ThemeMode get themeMode => _themeMode;

  String _selectedEquipmentId;
  String get selectedEquipmentId => _selectedEquipmentId;

  static Future<AppState> create() async {
    final prefs = await SharedPreferences.getInstance();
    final store = SettingsStore(prefs);
    return AppState(
      store: store,
      settings: store.load(),
      themeMode: store.loadThemeMode(),
      selectedEquipmentId: store.loadSelectedEquipment(),
    );
  }

  void start() => monitor.start();

  Future<void> saveSettings(ServerSettings next) async {
    if (next == _settings) {
      await _store.save(next);
      return;
    }
    _settings = next;
    api.baseUrl = next.baseUrl;
    await _store.save(next);
    notifyListeners();
    monitor.check();
  }

  Future<void> setThemeMode(ThemeMode mode) async {
    if (mode == _themeMode) {
      await _store.saveThemeMode(mode);
      return;
    }
    _themeMode = mode;
    await _store.saveThemeMode(mode);
    notifyListeners();
  }

  /// Циклическое переключение темы: Системная → Тёмная → Светлая.
  Future<void> cycleThemeMode() async {
    final next = switch (_themeMode) {
      ThemeMode.system => ThemeMode.dark,
      ThemeMode.dark => ThemeMode.light,
      ThemeMode.light => ThemeMode.system,
    };
    await setThemeMode(next);
  }

  Future<void> selectEquipment(String equipmentId) async {
    if (equipmentId.isEmpty || equipmentId == _selectedEquipmentId) return;
    _selectedEquipmentId = equipmentId;
    await _store.saveSelectedEquipment(equipmentId);
    notifyListeners();
  }

  Future<void> checkConnection() => monitor.check();

  ConnectionStatus get connectionStatus => monitor.status;
  bool get isOnline => monitor.status == ConnectionStatus.online;

  void _onMonitorChanged() => notifyListeners();

  @override
  void dispose() {
    monitor.removeListener(_onMonitorChanged);
    monitor.dispose();
    api.dispose();
    super.dispose();
  }
}

/// Доступ к [AppState] из любого экрана.
class AppScope extends InheritedNotifier<AppState> {
  const AppScope({super.key, required AppState state, required super.child})
      : super(notifier: state);

  static AppState of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'AppScope не найден в дереве виджетов');
    return scope!.notifier!;
  }

  static AppState? maybeOf(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    return scope?.notifier;
  }
}
