apiVersion: apps/v1
kind: Deployment
metadata:
  name: frostgate-core
  namespace: frostgate-dev
  labels:
    app: frostgate-core
spec:
  replicas: 1
  selector:
    matchLabels:
      app: frostgate-core
  template:
    metadata:
      labels:
        app: frostgate-core
    spec:
      containers:
        - name: frostgate-core
          image: registry.internal/frostgate/frostgate-core:dev-dev-local
          imagePullPolicy: IfNotPresent
          envFrom:
            - secretRef:
                name: frostgate-core-env
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 5
            failureThreshold: 5
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 5
