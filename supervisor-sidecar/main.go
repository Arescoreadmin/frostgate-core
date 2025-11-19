package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

type coreHealth struct {
	Status          string `json:"status"`
	Env             string `json:"env"`
	EnforcementMode string `json:"enforcement_mode"`
}

type coreStatus struct {
	Service         string      `json:"service"`
	Version         string      `json:"version"`
	Env             string      `json:"env"`
	EnforcementMode string      `json:"enforcement_mode"`
	Components      interface{} `json:"components"`
	Anchor          interface{} `json:"anchor"`
}

type supervisorStatus struct {
	Status         string       `json:"status"`
	CoreReachable  bool         `json:"core_reachable"`
	CoreHealth     *coreHealth  `json:"core_health,omitempty"`
	CoreStatus     *coreStatus  `json:"core_status,omitempty"`
	LastCheck      time.Time    `json:"last_check"`
	Errors         []string     `json:"errors,omitempty"`
	EnforcementMode string      `json:"enforcement_mode,omitempty"`
}

var (
	coreBaseURL string
)

func getenv(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func fetchJSON(url string, target interface{}) error {
	client := &http.Client{
		Timeout: 2 * time.Second,
	}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return &httpError{StatusCode: resp.StatusCode}
	}

	return json.NewDecoder(resp.Body).Decode(target)
}

type httpError struct {
	StatusCode int
}

func (e *httpError) Error() string {
	return http.StatusText(e.StatusCode)
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	resp := map[string]string{
		"status": "ok",
		"component": "supervisor-sidecar",
	}
	_ = json.NewEncoder(w).Encode(resp)
}

func handleSupervisorStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	status := supervisorStatus{
		Status:        "degraded",
		CoreReachable: false,
		LastCheck:     time.Now().UTC(),
		Errors:        []string{},
	}

	// /health
	var h coreHealth
	if err := fetchJSON(coreBaseURL+"/health", &h); err != nil {
		status.Errors = append(status.Errors, "health: "+err.Error())
	} else {
		status.CoreReachable = true
		status.CoreHealth = &h
		status.EnforcementMode = h.EnforcementMode
	}

	// /status
	var s coreStatus
	if err := fetchJSON(coreBaseURL+"/status", &s); err != nil {
		status.Errors = append(status.Errors, "status: "+err.Error())
	} else {
		status.CoreStatus = &s
	}

	if status.CoreReachable && len(status.Errors) == 0 {
		status.Status = "ok"
	}

	_ = json.NewEncoder(w).Encode(status)
}

func main() {
	log.Println("FrostGate supervisor-sidecar starting...")

	coreBaseURL = getenv("FG_CORE_BASE_URL", "http://127.0.0.1:8080")
	addr := getenv("SUPERVISOR_LISTEN_ADDR", ":9090")

	http.HandleFunc("/health", handleHealth)
	http.HandleFunc("/supervisor/status", handleSupervisorStatus)

	log.Printf("Supervisor-sidecar listening on %s, coreBaseURL=%s\n", addr, coreBaseURL)
	if err := http.ListenAndServe(addr, nil); err != nil {
		log.Fatalf("supervisor-sidecar failed: %v", err)
	}
}
