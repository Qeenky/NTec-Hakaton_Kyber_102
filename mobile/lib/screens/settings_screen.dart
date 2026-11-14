import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../models/labels.dart';
import '../services/server_settings.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_indicator.dart';
import '../widgets/status_chip.dart';

/// Экран настроек: адрес локального сервера, проверка связи и выбор темы.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _controller;
  String? _error;
  bool _checking = false;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_initialized) {
      _controller.text = AppScope.of(context).settings.authority;
      _initialized = true;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save({bool showSnack = true}) async {
    final app = AppScope.of(context);
    final parsed = ServerSettings.tryParse(_controller.text);
    if (parsed == null) {
      setState(() => _error =
          'Введите адрес в формате IP:порт, например ${ServerSettings.defaultAddress}');
      return;
    }
    setState(() => _error = null);
    _controller.text = parsed.authority;
    await app.saveSettings(parsed);
    if (!mounted) return;
    if (showSnack) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Адрес сохранён: ${parsed.authority}')),
      );
    }
    await app.checkConnection();
  }

  Future<void> _check() async {
    if (_checking) return;
    setState(() {
      _checking = true;
      _error = null;
    });
    await _save(showSnack: false);
    if (!mounted) return;
    final app = AppScope.of(context);
    final palette = AppPalette.of(context);
    setState(() => _checking = false);
    if (app.isOnline) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Связь есть · ${app.monitor.health?.summary ?? ''}'),
          backgroundColor: palette.surfaceHigh,
        ),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(app.monitor.error ?? 'Сервер недоступен'),
          backgroundColor: palette.errorSurface,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final palette = AppPalette.of(context);
    final health = app.monitor.health;
    final online = app.isOnline;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Подключение'),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 8),
            child: Center(child: ConnectionIndicator(showHost: true)),
          ),
          IconButton(
            tooltip: 'Проверить связь',
            onPressed: _checking ? null : _check,
            icon: _checking
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          Text(
            'ТЕМА ОФОРМЛЕНИЯ',
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.8,
            ),
          ),
          const SizedBox(height: 8),
          Panel(
            padding: const EdgeInsets.all(10),
            child: Row(
              children: [
                Expanded(
                  child: _ThemeModeOption(
                    key: const Key('theme-mode-system'),
                    icon: Icons.brightness_auto_outlined,
                    label: 'Системная',
                    selected: app.themeMode == ThemeMode.system,
                    onTap: () => app.setThemeMode(ThemeMode.system),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ThemeModeOption(
                    key: const Key('theme-mode-dark'),
                    icon: Icons.dark_mode_outlined,
                    label: 'Тёмная',
                    selected: app.themeMode == ThemeMode.dark,
                    onTap: () => app.setThemeMode(ThemeMode.dark),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ThemeModeOption(
                    key: const Key('theme-mode-light'),
                    icon: Icons.light_mode_outlined,
                    label: 'Светлая',
                    selected: app.themeMode == ThemeMode.light,
                    onTap: () => app.setThemeMode(ThemeMode.light),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          Text(
            'АДРЕС СЕРВЕРА',
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.8,
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('server-address-field'),
            controller: _controller,
            keyboardType: TextInputType.url,
            textInputAction: TextInputAction.done,
            autocorrect: false,
            onSubmitted: (_) => _save(),
            style: TextStyle(
              color: palette.text,
              fontSize: 20,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.4,
            ),
            decoration: InputDecoration(
              hintText: ServerSettings.defaultAddress,
              errorText: _error,
              prefixIcon: const Icon(Icons.dns_outlined),
              suffixIcon: IconButton(
                tooltip: 'Очистить',
                icon: const Icon(Icons.close),
                onPressed: () => _controller.clear(),
              ),
            ),
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  key: const Key('save-address-button'),
                  onPressed: _save,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('Сохранить'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  key: const Key('check-connection-button'),
                  onPressed: _checking ? null : _check,
                  icon: _checking
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.wifi_tethering),
                  label: const Text('Проверить связь'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 18),
          Panel(
            borderColor: (online ? palette.green : palette.red)
                .withValues(alpha: 0.55),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      online
                          ? Icons.check_circle_outline
                          : Icons.error_outline,
                      color: online ? palette.green : palette.red,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        online ? 'Связь установлена' : 'Сервер недоступен',
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                          color: online ? palette.green : palette.red,
                        ),
                      ),
                    ),
                    if (health?.version != null)
                      StatusChip(
                        label: 'v${health!.version}',
                        color: palette.accent,
                        dense: true,
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  online
                      ? (health?.summary.isNotEmpty == true
                          ? health!.summary
                          : 'Сервер отвечает на /health')
                      : (app.monitor.error ??
                          'Проверьте Wi-Fi и адрес. Сервер должен слушать 0.0.0.0:8000.'),
                  style: TextStyle(
                    color: palette.textSecondary,
                    fontSize: 14,
                    height: 1.35,
                  ),
                ),
                if (online) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      StatusChip(
                        label:
                            'AR: ${health?.pipelineEnabled == true ? 'включён' : 'выключен'}',
                        color: health?.pipelineEnabled == true
                            ? palette.green
                            : palette.grey,
                        dense: true,
                      ),
                      StatusChip(
                        label:
                            'Детектор: ${detectorLabel(health?.detectorMode ?? health?.detector)}',
                        color: palette.accent,
                        dense: true,
                      ),
                    ],
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 18),
          Text(
            'КАК ПОДКЛЮЧИТЬСЯ',
            style: TextStyle(
              color: palette.textMuted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.8,
            ),
          ),
          const SizedBox(height: 8),
          const Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _HintRow(
                  icon: Icons.wifi,
                  title: 'Wi-Fi с ноутбука',
                  text:
                      'Включите на ноутбуке мобильную точку доступа Windows. '
                      'Адрес по умолчанию — ${ServerSettings.defaultAddress}. '
                      'Интернет не нужен: связь идёт по локальной сети.',
                ),
                SizedBox(height: 14),
                _HintRow(
                  icon: Icons.usb,
                  title: 'USB (резерв)',
                  text:
                      'Подключите телефон по USB и выполните на компьютере: '
                      'adb reverse tcp:8000 tcp:8000. '
                      'В приложении укажите ${ServerSettings.usbAddress}.',
                ),
                SizedBox(height: 14),
                _HintRow(
                  icon: Icons.lock_outline,
                  title: 'Закрытый контур',
                  text:
                      'Приложение не обращается в интернет: только на указанный '
                      'адрес сервера. Все данные остаются в локальной сети.',
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          TextButton.icon(
            onPressed: () {
              _controller.text = ServerSettings.usbAddress;
              setState(() => _error = null);
            },
            icon: const Icon(Icons.settings_ethernet),
            label: const Text('Подставить адрес для USB: 127.0.0.1:8000'),
          ),
        ],
      ),
    );
  }
}

class _ThemeModeOption extends StatelessWidget {
  const _ThemeModeOption({
    super.key,
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final selectedBg =
        palette.isDark ? const Color(0xFF0F2135) : const Color(0xFFDCEAFB);
    return Material(
      color: selected ? selectedBg : palette.surfaceHigh,
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Container(
          height: 48,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(10),
            border: Border.all(
              color: selected ? palette.primary : palette.outline,
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                icon,
                size: 18,
                color: selected ? palette.primary : palette.textMuted,
              ),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: selected ? palette.primary : palette.text,
                    fontSize: 13,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _HintRow extends StatelessWidget {
  const _HintRow({
    required this.icon,
    required this.title,
    required this.text,
  });

  final IconData icon;
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: palette.accent, size: 20),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: TextStyle(
                  color: palette.text,
                  fontSize: 15,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                text,
                style: TextStyle(
                  color: palette.textSecondary,
                  fontSize: 13,
                  height: 1.35,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
