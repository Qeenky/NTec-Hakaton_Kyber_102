import 'package:material_ui/material_ui.dart';

/// Единая палитра приложения (константы тёмной темы для обратной совместимости
/// и статических ссылок). Для адаптивных экранов используйте [AppPalette.of].
class AppColors {
  const AppColors._();

  // Нейтральная тёмная шкала (по tokens.json).
  static const Color background = Color(0xFF0B0F14);
  static const Color surface = Color(0xFF141B23);
  static const Color surfaceHigh = Color(0xFF1C2630);
  static const Color outline = Color(0xFF2E3A47);
  static const Color outlineStrong = Color(0xFF55697E);
  static const Color text = Color(0xFFF2F5F8);
  static const Color textSecondary = Color(0xFFA9B8C7);
  static const Color textMuted = Color(0xFF8595A6);
  static const Color scrim = Color(0xFF000000);
  static const Color errorSurface = Color(0xFF3A1416);

  // Фирменный синий КСТ: глубокий (#135AA6) — заливки и светлая тема,
  // светлый (#4C9EEA) — интерактивный акцент на тёмном фоне.
  static const Color brand = Color(0xFF135AA6);
  static const Color primary = Color(0xFF4C9EEA);
  static const Color onPrimary = Color(0xFFFFFFFF);
  static const Color accent = Color(0xFF35B8E0);

  // Семантические статусы (тёмная тема, контраст ≥ 6.2:1).
  static const Color green = Color(0xFF3DD68C);
  static const Color yellow = Color(0xFFFFC53D);
  static const Color orange = Color(0xFFFB923C);
  static const Color red = Color(0xFFFF6B6B);
  static const Color grey = Color(0xFF8B99A6);

  // Светлая тема (по tokens.json, контраст ≥ 5.3:1 на #FFFFFF / #F4F6F8).
  static const Color lightBackground = Color(0xFFF4F6F8);
  static const Color lightSurface = Color(0xFFFFFFFF);
  static const Color lightSurfaceHigh = Color(0xFFE8EDF2);
  static const Color lightOutline = Color(0xFFC9D3DD);
  static const Color lightOutlineStrong = Color(0xFF5C6B7A);
  static const Color lightText = Color(0xFF14181C);
  static const Color lightTextSecondary = Color(0xFF3F4C59);
  static const Color lightTextMuted = Color(0xFF5C6B7A);
  static const Color lightPrimary = Color(0xFF135AA6);
  static const Color lightAccent = Color(0xFF0B6E8A);
  static const Color lightGreen = Color(0xFF137A4B);
  static const Color lightYellow = Color(0xFF8A5A00);
  static const Color lightOrange = Color(0xFFB85000);
  static const Color lightRed = Color(0xFFB4232A);
  static const Color lightGrey = Color(0xFF5C6B7A);
  static const Color lightErrorSurface = Color(0xFFFDECEC);

  /// Цвет по строке из контракта (`color` оверлея или `status` узла).
  static Color byName(String? name, {Color fallback = grey}) {
    switch (name) {
      case 'green':
      case 'ok':
      case 'no_visible_defects':
        return green;
      case 'yellow':
      case 'check_required':
      case 'attention_required':
      case 'warning':
        return yellow;
      case 'orange':
        return orange;
      case 'red':
      case 'possible_defect':
      case 'defects_found':
      case 'critical':
      case 'defect':
        return red;
      case 'blue':
        return primary;
      case 'grey':
      case 'insufficient_data':
      case 'info':
        return grey;
      default:
        return fallback;
    }
  }
}

/// Адаптивная палитра для светлой и тёмной темы (контраст текста ≥ 4.5:1 по WCAG AA).
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.brightness,
    required this.background,
    required this.surface,
    required this.surfaceHigh,
    required this.outline,
    required this.outlineStrong,
    required this.text,
    required this.textSecondary,
    required this.textMuted,
    required this.brand,
    required this.primary,
    required this.onPrimary,
    required this.accent,
    required this.green,
    required this.yellow,
    required this.orange,
    required this.red,
    required this.grey,
    required this.errorSurface,
  });

  final Brightness brightness;
  final Color background;
  final Color surface;
  final Color surfaceHigh;
  final Color outline;
  final Color outlineStrong;
  final Color text;
  final Color textSecondary;
  final Color textMuted;
  final Color brand;
  final Color primary;
  final Color onPrimary;
  final Color accent;
  final Color green;
  final Color yellow;
  final Color orange;
  final Color red;
  final Color grey;
  final Color errorSurface;

  bool get isDark => brightness == Brightness.dark;

  static const AppPalette dark = AppPalette(
    brightness: Brightness.dark,
    background: AppColors.background,
    surface: AppColors.surface,
    surfaceHigh: AppColors.surfaceHigh,
    outline: AppColors.outline,
    outlineStrong: AppColors.outlineStrong,
    text: AppColors.text,
    textSecondary: AppColors.textSecondary,
    textMuted: AppColors.textMuted,
    brand: AppColors.brand,
    primary: AppColors.primary,
    onPrimary: AppColors.onPrimary,
    accent: AppColors.accent,
    green: AppColors.green,
    yellow: AppColors.yellow,
    orange: AppColors.orange,
    red: AppColors.red,
    grey: AppColors.grey,
    errorSurface: AppColors.errorSurface,
  );

  static const AppPalette light = AppPalette(
    brightness: Brightness.light,
    background: AppColors.lightBackground,
    surface: AppColors.lightSurface,
    surfaceHigh: AppColors.lightSurfaceHigh,
    outline: AppColors.lightOutline,
    outlineStrong: AppColors.lightOutlineStrong,
    text: AppColors.lightText,
    textSecondary: AppColors.lightTextSecondary,
    textMuted: AppColors.lightTextMuted,
    brand: AppColors.brand,
    primary: AppColors.lightPrimary,
    onPrimary: AppColors.onPrimary,
    accent: AppColors.lightAccent,
    green: AppColors.lightGreen,
    yellow: AppColors.lightYellow,
    orange: AppColors.lightOrange,
    red: AppColors.lightRed,
    grey: AppColors.lightGrey,
    errorSurface: AppColors.lightErrorSurface,
  );

  static AppPalette of(BuildContext context) {
    final theme = Theme.of(context);
    return theme.extension<AppPalette>() ??
        (theme.brightness == Brightness.light ? light : dark);
  }

  /// Семантический цвет статуса с гарантированным контрастом ≥ 4.5:1 в активной теме.
  Color byName(String? name, {Color? fallback}) {
    switch (name) {
      case 'green':
      case 'ok':
      case 'no_visible_defects':
        return green;
      case 'yellow':
      case 'check_required':
      case 'attention_required':
      case 'warning':
        return yellow;
      case 'orange':
        return orange;
      case 'red':
      case 'possible_defect':
      case 'defects_found':
      case 'critical':
      case 'defect':
        return red;
      case 'blue':
        return primary;
      case 'grey':
      case 'insufficient_data':
      case 'info':
        return grey;
      default:
        return fallback ?? grey;
    }
  }

  /// Адаптирует переданный статический цвет [AppColors] под текущую тему.
  Color adaptColor(Color color) {
    if (isDark) return color;
    if (color == AppColors.green) return green;
    if (color == AppColors.yellow) return yellow;
    if (color == AppColors.orange) return orange;
    if (color == AppColors.red) return red;
    if (color == AppColors.grey) return grey;
    if (color == AppColors.primary) return primary;
    if (color == AppColors.accent) return accent;
    if (color == AppColors.text) return text;
    if (color == AppColors.textMuted) return textMuted;
    if (color == AppColors.surface) return surface;
    if (color == AppColors.surfaceHigh) return surfaceHigh;
    if (color == AppColors.background) return background;
    if (color == AppColors.outline) return outline;
    return color;
  }

  @override
  AppPalette copyWith({
    Brightness? brightness,
    Color? background,
    Color? surface,
    Color? surfaceHigh,
    Color? outline,
    Color? outlineStrong,
    Color? text,
    Color? textSecondary,
    Color? textMuted,
    Color? brand,
    Color? primary,
    Color? onPrimary,
    Color? accent,
    Color? green,
    Color? yellow,
    Color? orange,
    Color? red,
    Color? grey,
    Color? errorSurface,
  }) {
    return AppPalette(
      brightness: brightness ?? this.brightness,
      background: background ?? this.background,
      surface: surface ?? this.surface,
      surfaceHigh: surfaceHigh ?? this.surfaceHigh,
      outline: outline ?? this.outline,
      outlineStrong: outlineStrong ?? this.outlineStrong,
      text: text ?? this.text,
      textSecondary: textSecondary ?? this.textSecondary,
      textMuted: textMuted ?? this.textMuted,
      brand: brand ?? this.brand,
      primary: primary ?? this.primary,
      onPrimary: onPrimary ?? this.onPrimary,
      accent: accent ?? this.accent,
      green: green ?? this.green,
      yellow: yellow ?? this.yellow,
      orange: orange ?? this.orange,
      red: red ?? this.red,
      grey: grey ?? this.grey,
      errorSurface: errorSurface ?? this.errorSurface,
    );
  }

  @override
  AppPalette lerp(ThemeExtension<AppPalette>? other, double t) {
    if (other is! AppPalette) return this;
    return t < 0.5 ? this : other;
  }
}

/// Шкала отступов: 4 / 8 / 12 / 16 / 24.
class AppSpacing {
  const AppSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
}

ThemeData buildDarkTheme() => buildAppTheme(brightness: Brightness.dark);

ThemeData buildLightTheme() => buildAppTheme(brightness: Brightness.light);

ThemeData buildAppTheme({Brightness brightness = Brightness.dark}) {
  final isDark = brightness == Brightness.dark;
  final palette = isDark ? AppPalette.dark : AppPalette.light;

  final scheme = ColorScheme.fromSeed(
    seedColor: palette.brand,
    brightness: brightness,
  ).copyWith(
    primary: palette.brand,
    onPrimary: Colors.white,
    secondary: palette.accent,
    onSecondary: Colors.white,
    primaryContainer: isDark ? const Color(0xFF0F2135) : const Color(0xFFDCEAFB),
    onPrimaryContainer: isDark ? palette.primary : palette.brand,
    surface: palette.surface,
    onSurface: palette.text,
    surfaceContainerHighest: palette.surfaceHigh,
    onSurfaceVariant: palette.textMuted,
    error: palette.red,
    onError: Colors.white,
    outline: palette.outline,
  );

  final base = ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    scaffoldBackgroundColor: palette.background,
    canvasColor: palette.background,
    fontFamily: 'Roboto',
    visualDensity: VisualDensity.standard,
    extensions: <ThemeExtension<dynamic>>[palette],
  );

  return base.copyWith(
    textTheme: base.textTheme
        .apply(bodyColor: palette.text, displayColor: palette.text)
        .copyWith(
          displayLarge: TextStyle(
            fontSize: 34,
            fontWeight: FontWeight.w800,
            letterSpacing: -1.0,
            height: 1.05,
            color: palette.text,
          ),
          headlineLarge: TextStyle(
            fontSize: 28,
            fontWeight: FontWeight.w800,
            letterSpacing: -0.5,
            height: 1.14,
            color: palette.text,
          ),
          headlineMedium: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.w700,
            letterSpacing: -0.3,
            height: 1.17,
            color: palette.text,
          ),
          titleLarge: TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.w700,
            letterSpacing: -0.2,
            height: 1.25,
            color: palette.text,
          ),
          titleMedium: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0,
            height: 1.35,
            color: palette.text,
          ),
          bodyLarge: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w400,
            height: 1.38,
            color: palette.text,
          ),
          bodyMedium: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w400,
            height: 1.38,
            color: palette.text,
          ),
          bodySmall: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w500,
            height: 1.33,
            color: palette.textMuted,
          ),
          labelLarge: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.1,
            color: palette.text,
          ),
          labelMedium: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.1,
            color: palette.text,
          ),
          labelSmall: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.2,
            color: palette.textMuted,
          ),
        ),
    appBarTheme: AppBarTheme(
      backgroundColor: palette.background,
      foregroundColor: palette.text,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: palette.text,
        letterSpacing: -0.2,
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 64,
      backgroundColor: palette.surface,
      indicatorColor: isDark
          ? const Color(0xFF0F2135)
          : const Color(0xFFDCEAFB),
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return TextStyle(
          fontSize: 12,
          fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
          color: selected ? palette.primary : palette.textMuted,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return IconThemeData(
          size: 22,
          color: selected ? palette.primary : palette.textMuted,
        );
      }),
    ),
    bottomNavigationBarTheme: BottomNavigationBarThemeData(
      backgroundColor: palette.surface,
      selectedItemColor: palette.primary,
      unselectedItemColor: palette.textMuted,
      selectedLabelStyle: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w700,
      ),
      unselectedLabelStyle: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w600,
      ),
      type: BottomNavigationBarType.fixed,
      elevation: 0,
    ),
    dividerTheme: DividerThemeData(
      color: palette.outline,
      thickness: 1,
      space: 1,
    ),
    cardTheme: CardThemeData(
      color: palette.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: palette.brand,
        foregroundColor: Colors.white,
        disabledBackgroundColor: palette.surfaceHigh,
        disabledForegroundColor: palette.textMuted,
        minimumSize: const Size.fromHeight(52),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(48),
        foregroundColor: palette.text,
        side: BorderSide(color: palette.outlineStrong, width: 1.5),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: palette.primary,
        minimumSize: const Size(48, 48),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: palette.surfaceHigh,
      labelStyle: TextStyle(color: palette.textMuted, fontSize: 13),
      hintStyle: TextStyle(color: palette.textMuted, fontSize: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: palette.outline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: palette.outline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: palette.primary, width: 2),
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: palette.surfaceHigh,
      contentTextStyle: TextStyle(color: palette.text, fontSize: 14),
      behavior: SnackBarBehavior.floating,
    ),
    dialogTheme: DialogThemeData(backgroundColor: palette.surface),
    listTileTheme: ListTileThemeData(
      textColor: palette.text,
      iconColor: palette.textMuted,
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: palette.primary,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: palette.surfaceHigh,
      selectedColor: isDark
          ? const Color(0xFF0F2135)
          : const Color(0xFFDCEAFB),
      side: BorderSide(color: palette.outline),
      labelStyle: TextStyle(color: palette.text, fontSize: 13, fontWeight: FontWeight.w600),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
    ),
  );
}

/// Базовая поверхность карточки с одним тонким контуром (адаптируется к теме).
class Panel extends StatelessWidget {
  const Panel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.lg),
    this.color,
    this.borderColor,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final Color? borderColor;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final effectiveColor = color == null
        ? palette.surface
        : (color == AppColors.surface ? palette.surface : color!);
    final effectiveBorder = borderColor ?? palette.outline;
    final content = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: effectiveColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: effectiveBorder),
      ),
      child: child,
    );
    if (onTap == null) return content;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: content,
      ),
    );
  }
}
