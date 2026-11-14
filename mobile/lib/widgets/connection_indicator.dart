import 'package:material_ui/material_ui.dart';

import '../app_state.dart';
import '../services/connection_monitor.dart';
import '../theme/app_theme.dart';

/// Индикатор связи в шапке. Показывает состояние /health и (опционально) хост.
class ConnectionIndicator extends StatelessWidget {
  const ConnectionIndicator({super.key, this.showHost = false});

  final bool showHost;

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final palette = AppPalette.of(context);
    final status = app.connectionStatus;
    final color = switch (status) {
      ConnectionStatus.online => palette.green,
      ConnectionStatus.offline => palette.red,
      ConnectionStatus.checking || ConnectionStatus.unknown => palette.yellow,
    };
    final text = switch (status) {
      ConnectionStatus.online => 'В сети',
      ConnectionStatus.offline => 'Нет связи',
      ConnectionStatus.checking => 'Проверка…',
      ConnectionStatus.unknown => '—',
    };

    return Semantics(
      label: 'Связь: $text, сервер ${app.settings.authority}',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: palette.surfaceHigh,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.55)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: color,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 6),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  text,
                  style: TextStyle(
                    color: color,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    height: 1.1,
                  ),
                ),
                if (showHost)
                  Text(
                    app.settings.authority,
                    style: TextStyle(
                      color: palette.textMuted,
                      fontSize: 12,
                      height: 1.1,
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Кнопка переключения темы в шапке (Системная / Тёмная / Светлая).
class ThemeModeButton extends StatelessWidget {
  const ThemeModeButton({super.key});

  @override
  Widget build(BuildContext context) {
    final app = AppScope.maybeOf(context);
    if (app == null) return const SizedBox.shrink();
    final mode = app.themeMode;
    final icon = switch (mode) {
      ThemeMode.dark => Icons.dark_mode_outlined,
      ThemeMode.light => Icons.light_mode_outlined,
      ThemeMode.system => Icons.brightness_auto_outlined,
    };
    final label = switch (mode) {
      ThemeMode.dark => 'Тема: тёмная',
      ThemeMode.light => 'Тема: светлая',
      ThemeMode.system => 'Тема: системная',
    };
    return IconButton(
      key: const Key('theme-toggle-button'),
      tooltip: label,
      onPressed: app.cycleThemeMode,
      icon: Icon(icon),
    );
  }
}
