"""Personalized supervised classification from labelled webcam gaze samples."""
import numpy as np

LABELS = ("LEFT", "CENTER", "RIGHT")
MODEL_VERSION = 1


def eye_features(landmarks, width, height):
    """Project each iris onto its own eye-corner axis to compensate for roll."""
    if landmarks is None or len(landmarks) < 478:
        return None
    ratios = []
    for first, second, iris in ((33, 133, 468), (362, 263, 473)):
        start, end, pupil = (landmarks[i] for i in (first, second, iris))
        dx = (end.x - start.x) * width
        dy = (end.y - start.y) * height
        length_squared = dx * dx + dy * dy
        if not np.isfinite(length_squared) or length_squared < 36:
            return None
        x = (pupil.x - start.x) * width
        y = (pupil.y - start.y) * height
        projection = (x * dx + y * dy) / length_squared
        if not .03 <= projection <= .97:
            return None
        ratios.append(projection)
    return tuple(ratios)


class GazeModel:
    """Learn three class centres and pooled per-eye noise from labelled data."""

    def __init__(self, centres, variance):
        noise_floor = .016
        self.centres = np.asarray(centres, dtype=float)
        variance = np.asarray(variance, dtype=float)
        if (self.centres.shape != (3, 2) or variance.shape != (2,)
                or not np.isfinite(self.centres).all() or not np.isfinite(variance).all()
                or np.any(self.centres < 0) or np.any(self.centres > 1)
                or np.any(variance < noise_floor ** 2) or np.any(variance > 1)):
            raise ValueError("Invalid saved gaze model.")
        self.variance = variance
        self.inverse_variance = 1 / self.variance

    @classmethod
    def fit(cls, training, validation):
        groups = [np.asarray(training[label], dtype=float) for label in LABELS]
        checks = [np.asarray(validation[label], dtype=float) for label in LABELS]
        for label, train, check in zip(LABELS, groups, checks):
            for samples in (train, check):
                if samples.ndim != 2 or samples.shape[1] != 2 or len(samples) < 6:
                    raise ValueError(f"Too few clear {label.lower()} samples. Keep both eyes visible and retry.")
        centres = np.array([np.median(group, axis=0) for group in groups])
        residuals = np.concatenate([group - centre for group, centre in zip(groups, centres)])
        noise_floor = .016
        variance = np.maximum(np.mean(residuals ** 2, axis=0), noise_floor ** 2)
        model = cls(centres, variance)
        separation = np.linalg.norm(np.diff(centres, axis=0), axis=1)
        if min(separation) < .025:
            raise ValueError("The three directions looked too similar. Follow the dots with your eyes and retry.")
        for label, samples in zip(LABELS, checks):
            correct = sum(model.predict(sample) == label for sample in samples)
            if correct / len(samples) < .75:
                raise ValueError(f"{label.title()} validation failed ({correct}/{len(samples)}). Follow the dot and retry.")
        return model

    def predict(self, features):
        if features is None:
            return None
        distances = np.sum((self.centres - features) ** 2 * self.inverse_variance, axis=1)
        if not np.isfinite(distances).all():
            return None
        best, second, _ = np.argsort(distances)
        if distances[best] > 36 or distances[second] - distances[best] < 1:
            return None
        return LABELS[best]

    def to_dict(self):
        return {"version": MODEL_VERSION, "centres": self.centres.tolist(),
                "variance": self.variance.tolist()}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("version") != MODEL_VERSION:
            raise ValueError("Unsupported saved gaze model.")
        return cls(data.get("centres"), data.get("variance"))
