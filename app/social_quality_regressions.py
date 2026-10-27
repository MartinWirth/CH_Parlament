"""Read-only deterioration signals across consecutive stored social check runs."""
from .social_check_history import LANGUAGES, MAX_RUNS
from .social_quality_trends import quality_trends


def quality_regressions(language, *, limit=10, reachability_drop=10, flagged_rise=10,
                        duplicate_increase=1, invalid_increase=1):
    if language not in LANGUAGES or type(limit) is not int or not 2 <= limit <= MAX_RUNS:
        raise ValueError("Invalid regression query")
    thresholds = (reachability_drop, flagged_rise, duplicate_increase, invalid_increase)
    if any(type(value) is not int or value < 0 or value > maximum
           for value, maximum in zip(thresholds, (100, 100, 10000, 10000))):
        raise ValueError("Invalid regression thresholds")
    points = quality_trends(language, limit=limit)['trend']
    warnings = []
    for before, after in zip(points, points[1:]):
        # A changed checked-link set can change the rates without a true regression.
        # Surface this explicitly and avoid treating incomplete data as zero.
        comparisons = (
            ('reachability_drop', 'reachable_percent', reachability_drop, -1),
            ('flagged_rise', 'flagged_percent', flagged_rise, 1),
            ('duplicate_increase', 'duplicates', duplicate_increase, 1),
            ('invalid_increase', 'invalid', invalid_increase, 1),
        )
        for code, field, threshold, direction in comparisons:
            old, new = before[field], after[field]
            if old is None or new is None:
                continue
            delta = round(new - old, 1)
            if direction * delta > 0 and direction * delta >= threshold:
                warnings.append({
                    'code': code, 'previous_at': before['checked_at'],
                    'checked_at': after['checked_at'], 'previous': old, 'current': new,
                    'delta': delta, 'threshold': threshold,
                    'sample_changed': before['checked'] != after['checked'],
                    'truncated': before['truncated'] or after['truncated'],
                })
    latest = points[-1]['checked_at'] if points else None
    return {
        'language': language, 'runs': len(points), 'latest_checked_at': latest,
        'thresholds': dict(zip(('reachability_drop', 'flagged_rise',
                                'duplicate_increase', 'invalid_increase'), thresholds)),
        'warnings': warnings,
        'latest_warnings': [item for item in warnings if item['checked_at'] == latest],
        'note': 'Signals compare consecutive stored runs. Percentages use only checked links; '
                'changed link samples and truncated runs limit comparability. '
                'Historical manual triage states are not reconstructed. '
                'Missing quality snapshots are not interpreted as zero.',
    }
