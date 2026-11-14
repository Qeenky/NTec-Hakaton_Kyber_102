import 'dart:math' as math;

/// Площадь многоугольника (shoelace).
double polygonArea(List<List<double>> points) {
  if (points.length < 3) return 0;
  var area = 0.0;
  for (var index = 0; index < points.length; index++) {
    final current = points[index];
    final next = points[(index + 1) % points.length];
    area += current[0] * next[1] - next[0] * current[1];
  }
  return area.abs() / 2;
}

/// Отсечение многоугольника прямоугольником (Sutherland–Hodgman).
List<List<double>> clipPolygonToRect(
  List<List<double>> points,
  double width,
  double height,
) {
  if (points.isEmpty) return const <List<double>>[];
  var output = List<List<double>>.from(points);

  List<List<double>> clipEdge(
    List<List<double>> input,
    bool Function(List<double> point) inside,
    List<double> Function(List<double> a, List<double> b) intersect,
  ) {
    final result = <List<double>>[];
    for (var index = 0; index < input.length; index++) {
      final current = input[index];
      final previous = input[(index - 1 + input.length) % input.length];
      final currentInside = inside(current);
      final previousInside = inside(previous);
      if (currentInside) {
        if (!previousInside) result.add(intersect(previous, current));
        result.add(current);
      } else if (previousInside) {
        result.add(intersect(previous, current));
      }
    }
    return result;
  }

  List<double> verticalIntersect(
    List<double> a,
    List<double> b,
    double x,
  ) {
    final t = (x - a[0]) / ((b[0] - a[0]) == 0 ? 1e-9 : (b[0] - a[0]));
    return <double>[x, a[1] + (b[1] - a[1]) * t];
  }

  List<double> horizontalIntersect(
    List<double> a,
    List<double> b,
    double y,
  ) {
    final t = (y - a[1]) / ((b[1] - a[1]) == 0 ? 1e-9 : (b[1] - a[1]));
    return <double>[a[0] + (b[0] - a[0]) * t, y];
  }

  output = clipEdge(output, (p) => p[0] >= 0, (a, b) => verticalIntersect(a, b, 0));
  output = clipEdge(
    output,
    (p) => p[0] <= width,
    (a, b) => verticalIntersect(a, b, width),
  );
  output = clipEdge(output, (p) => p[1] >= 0, (a, b) => horizontalIntersect(a, b, 0));
  output = clipEdge(
    output,
    (p) => p[1] <= height,
    (a, b) => horizontalIntersect(a, b, height),
  );
  return output;
}

/// Доля площади полигона, вышедшая за пределы кадра (0..1).
double outsideAreaRatio(
  List<List<double>> polygon,
  double width,
  double height,
) {
  final total = polygonArea(polygon);
  if (total <= 0 || width <= 0 || height <= 0) return 1;
  final clipped = clipPolygonToRect(polygon, width, height);
  final inside = polygonArea(clipped);
  return math.max(0, math.min(1, 1 - inside / total));
}
