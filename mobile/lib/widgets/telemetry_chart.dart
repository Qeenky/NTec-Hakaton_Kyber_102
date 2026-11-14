import 'package:fl_chart/fl_chart.dart';
import 'package:material_ui/material_ui.dart';

import '../theme/app_theme.dart';

/// Мини-график телеметрии за последние минуты.
class TelemetryChart extends StatelessWidget {
  const TelemetryChart({
    super.key,
    required this.title,
    required this.values,
    required this.unit,
    required this.color,
    this.latest,
    this.height = 68,
  });

  final String title;
  final List<double> values;
  final String unit;
  final Color color;
  final String? latest;
  final double height;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final lineColor = palette.adaptColor(color);
    final hasData = values.length >= 2;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: palette.textSecondary,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            Text(
              latest == null ? '—' : '$latest $unit',
              style: TextStyle(
                color: lineColor,
                fontSize: 15,
                fontWeight: FontWeight.w800,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        SizedBox(
          height: height,
          child: hasData
              ? LineChart(
                  LineChartData(
                    minX: 0,
                    maxX: (values.length - 1).toDouble(),
                    minY: _minY(values),
                    maxY: _maxY(values),
                    gridData: const FlGridData(show: false),
                    titlesData: const FlTitlesData(show: false),
                    borderData: FlBorderData(show: false),
                    lineTouchData: const LineTouchData(enabled: false),
                    lineBarsData: [
                      LineChartBarData(
                        spots: [
                          for (var index = 0; index < values.length; index++)
                            FlSpot(index.toDouble(), values[index]),
                        ],
                        isCurved: true,
                        curveSmoothness: 0.2,
                        color: lineColor,
                        barWidth: 2.2,
                        isStrokeCapRound: true,
                        dotData: const FlDotData(show: false),
                        belowBarData: BarAreaData(
                          show: true,
                          color: lineColor.withValues(alpha: 0.14),
                        ),
                      ),
                    ],
                  ),
                )
              : Container(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    'Нет данных за период',
                    style: TextStyle(color: palette.textMuted, fontSize: 12),
                  ),
                ),
        ),
      ],
    );
  }

  double _minY(List<double> data) {
    final min = data.reduce((a, b) => a < b ? a : b);
    final max = data.reduce((a, b) => a > b ? a : b);
    final span = (max - min).abs();
    final padding = span < 0.001 ? (max.abs() * 0.1 + 1) : span * 0.25;
    return min - padding;
  }

  double _maxY(List<double> data) {
    final min = data.reduce((a, b) => a < b ? a : b);
    final max = data.reduce((a, b) => a > b ? a : b);
    final span = (max - min).abs();
    final padding = span < 0.001 ? (max.abs() * 0.1 + 1) : span * 0.25;
    return max + padding;
  }
}
