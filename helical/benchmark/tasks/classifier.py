
from sklearn.preprocessing import LabelEncoder
from tensorflow.keras.utils import to_categorical
from sklearn.metrics import accuracy_score, precision_score, f1_score, recall_score
from sklearn.model_selection import train_test_split
from helical.benchmark.tasks.base_task import BaseTask
from helical.benchmark.task_models.base_task_model import BaseTaskModel
import numpy as np
import logging

LOGGER = logging.getLogger(__name__)

class Classifier(BaseTask):
    def __init__(self, labels: list[str], task_model: BaseTaskModel) -> None:
        self.num_types = len(set(labels))

        self.encoder = LabelEncoder()
        self.y_encoded = self.encoder.fit_transform(labels)
        self.y_encoded = to_categorical(self.y_encoded, num_classes=self.num_types)
        self.y_encoded.shape
        self.task_model = task_model
        self.trained_models = {}

    def train_task_models(self, x_train_embeddings: dict) -> None:

        for model_name, embeddings in x_train_embeddings.items():
            x = embeddings
            X_train, X_test, y_train, y_test = train_test_split(x, self.y_encoded, test_size=0.1, random_state=42)
            
            LOGGER.info(f"Training model head on '{model_name}' embeddings.")
            self.task_model.compile()
            trained_task_model = self.task_model.train(X_train, y_train, validation_data=(X_test, y_test))
            self.trained_models.update({model_name: trained_task_model}) 
            
    def get_predictions(self, x_eval_embeddings) -> dict:
        predictions = {}
        for model_name, embeddings in x_eval_embeddings.items():
            x = embeddings
            m = self.trained_models[model_name]
            
            LOGGER.info(f"Predicting labels for '{model_name}'.")
            predictions.update({model_name: m.predict(x)}) 
        return predictions

    def get_evaluations(self, res, eval_labels) -> dict:
        evaluations = {}

        for model_name, predictions in res.items():

            LOGGER.info(f"Evaluating predictions for '{model_name}'.")
            y_pred = np.argmax(predictions, axis=1)
            y_true = self.encoder.fit_transform(eval_labels)
            evaluation = {
                "accuracy": accuracy_score(y_true, y_pred),
                "precision": precision_score(y_true, y_pred, average='macro'),
                "f1": f1_score(y_true, y_pred, average='macro'),
                "recall": recall_score(y_true, y_pred, average='macro'),
            }
            evaluations.update({model_name: evaluation})
        
        return evaluations
