import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:ntec_inspector/theme/app_theme.dart';

/// Коэффициент контраста WCAG для двух непрозрачных цветов.
double _contrast(Color a, Color b) {
  final la = a.computeLuminance();
  final lb = b.computeLuminance();
  final hi = la > lb ? la : lb;
  final lo = la > lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

void main() {
  for (final entry in <String, ThemeData>{
    'тёмная': buildDarkTheme(),
    'светлая': buildLightTheme(),
  }.entries) {
    final name = entry.key;
    final theme = entry.value;

    test('$name: текст на кнопках контрастен фону', () {
      final scaffold = theme.scaffoldBackgroundColor;

      final filled = theme.filledButtonTheme.style!;
      final filledBg = filled.backgroundColor!.resolve(<WidgetState>{})!;
      final filledFg = filled.foregroundColor!.resolve(<WidgetState>{})!;
      expect(
        _contrast(filledBg, filledFg),
        greaterThanOrEqualTo(4.5),
        reason: 'FilledButton: $filledFg на $filledBg',
      );

      final outlined = theme.outlinedButtonTheme.style!;
      final outlinedFg = outlined.foregroundColor!.resolve(<WidgetState>{})!;
      expect(
        _contrast(scaffold, outlinedFg),
        greaterThanOrEqualTo(4.5),
        reason: 'OutlinedButton: $outlinedFg на $scaffold',
      );

      final text = theme.textButtonTheme.style!;
      final textFg = text.foregroundColor!.resolve(<WidgetState>{})!;
      expect(
        _contrast(scaffold, textFg),
        greaterThanOrEqualTo(4.5),
        reason: 'TextButton: $textFg на $scaffold',
      );
    });

    test('$name: подписи нижней навигации читаются', () {
      final nav = theme.navigationBarTheme;
      final bg = nav.backgroundColor ?? theme.scaffoldBackgroundColor;
      for (final selected in <bool>[true, false]) {
        final states = selected
            ? <WidgetState>{WidgetState.selected}
            : <WidgetState>{};
        final color = nav.labelTextStyle!.resolve(states)!.color!;
        expect(
          _contrast(bg, color),
          greaterThanOrEqualTo(4.5),
          reason: 'NavigationBar selected=$selected: $color на $bg',
        );
      }
    });

    test('$name: заголовок AppBar читается', () {
      final appBar = theme.appBarTheme;
      final bg = appBar.backgroundColor ?? theme.scaffoldBackgroundColor;
      final fg = appBar.foregroundColor ?? theme.colorScheme.onSurface;
      expect(_contrast(bg, fg), greaterThanOrEqualTo(4.5));
    });
  }

  test('AR-экран: светлый текст на чёрном фоне камеры', () {
    final theme = buildDarkTheme();
    final fg = theme.textTheme.bodyMedium!.color!;
    expect(_contrast(Colors.black, fg), greaterThanOrEqualTo(4.5));
  });
}
