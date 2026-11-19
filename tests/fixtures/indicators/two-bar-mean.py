# Synthetic fixture — two-bar mean
def compute(bars, params):
    points = []
    for index, bar in enumerate(bars):
        value = None if index == 0 else (bars[index - 1]['close'] + bar['close']) / params['divisor']
        points.append({'time_ms': bar['time_ms'], 'value': value})
    return {'series': [{'id': 'python-mean', 'pane': 'python-mean', 'type': 'histogram',
                        'style': {'color': '#123456', 'width': 2}, 'points': points}],
            'markers': [], 'regions': [], 'diagnostics': []}
