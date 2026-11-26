package client

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPutSendsTenantKeyAndDecodesNumbersExactly(t *testing.T) {
	var gotKey, gotPath string
	var gotBody Object
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey, gotPath = r.Header.Get("X-Apim-Tenant-Key"), r.URL.EscapedPath()
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &gotBody)
		_, _ = w.Write([]byte(`{"subscriptions_limit": 9007199254740993}`))
	}))
	defer server.Close()

	out, err := New(server.URL+"/", "tenant", nil).Put(context.Background(), "products/p%201", Object{"name": "P"})
	if err != nil {
		t.Fatal(err)
	}

	if gotKey != "tenant" || gotPath != "/apim/management/products/p%201" || gotBody["name"] != "P" {
		t.Fatalf("request = key %q path %q body %v", gotKey, gotPath, gotBody)
	}
	if out["subscriptions_limit"] != json.Number("9007199254740993") {
		t.Fatalf("number lost precision: %#v", out["subscriptions_limit"])
	}
}

func TestErrorsAreClassified(t *testing.T) {
	cases := map[string]struct {
		status int
		body   string
		check  func(error) bool
	}{
		"404 is ErrNotFound": {404, `{"detail":"Product not found"}`, func(err error) bool { return errors.Is(err, ErrNotFound) }},
		"422 keeps validation detail": {422, `{"detail":[{"msg":"Field required"}]}`, func(err error) bool {
			var apiErr *APIError
			return errors.As(err, &apiErr) && apiErr.Status == 422 && apiErr.Detail == `[{"msg":"Field required"}]`
		}},
		"gateway message is used": {409, `{"statusCode":409,"message":"Conflict"}`, func(err error) bool {
			var apiErr *APIError
			return errors.As(err, &apiErr) && apiErr.Detail == "Conflict"
		}},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer server.Close()

			_, err := New(server.URL, "", nil).Get(context.Background(), "products/p1")

			if err == nil || !tc.check(err) {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

func TestIsMasked(t *testing.T) {
	for value, want := range map[any]bool{"***": true, "[redacted]": false, "secret": false, nil: false, 3: false} {
		if IsMasked(value) != want {
			t.Errorf("IsMasked(%#v) = %v", value, !want)
		}
	}
}
