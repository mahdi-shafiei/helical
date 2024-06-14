from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.metrics import F1Score
from helical.benchmark.task_models.base_task_model import BaseTaskModel

class NeuralNetwork(BaseTaskModel):
    def __init__(self, input_shape: tuple, num_classes: int, loss: str = "categorical_crossentropy", learning_rate: float = 0.001, epochs=10, batch_size=32) -> None:
        self.input_shape = input_shape
        self.num_classes = num_classes
        self.loss = loss
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.batch_size = batch_size

    def compile(self):
        self.model = Sequential()
        self.model.add(Dense(256, activation='relu', input_shape=self.input_shape))
        self.model.add(Dropout(0.4))
        self.model.add(Dense(64, activation='relu'))
        self.model.add(Dropout(0.4))
        self.model.add(Dense(self.num_classes, activation='softmax'))

        self.optimizer = Adam(learning_rate=self.learning_rate)
        self.f1_metric = F1Score(average='macro')
        self.model.compile(loss=self.loss, optimizer=self.optimizer, metrics=self.f1_metric)

    def train(self, X_train, y_train,  validation_data) -> BaseTaskModel:
        self.model.fit(X_train, y_train, self.epochs, self.batch_size, validation_data)
        return self
    
    def predict(self, x):
        return self.model.predict(x)
    
    def get_model_summary(self):
        return self.model.summary()
    