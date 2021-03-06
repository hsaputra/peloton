// Copyright (c) 2019 Uber Technologies, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package label

import (
	"encoding/json"
	"fmt"

	"github.com/uber/peloton/.gen/peloton/api/v1alpha/peloton"
	"github.com/uber/peloton/.gen/thrift/aurora/api"
)

const _auroraMetadataKey = "aurora_metadata"

// NewAuroraMetadata creates a label for the original Aurora task metadata which
// was mapped into a Peloton job.
func NewAuroraMetadata(md []*api.Metadata) (*peloton.Label, error) {
	b, err := json.Marshal(md)
	if err != nil {
		return nil, fmt.Errorf("json marshal: %s", err)
	}
	return &peloton.Label{
		Key:   _auroraMetadataKey,
		Value: string(b),
	}, nil
}

// ParseAuroraMetadata converts Peloton label to a list of
// Aurora Metadata. The label for aurora metadata must be present, otherwise
// an error will be returned.
func ParseAuroraMetadata(ls []*peloton.Label) ([]*api.Metadata, error) {
	for _, l := range ls {
		if l.GetKey() == _auroraMetadataKey {
			var m []*api.Metadata
			err := json.Unmarshal([]byte(l.GetValue()), &m)
			if err != nil {
				return nil, err
			}
			return m, nil
		}
	}
	return nil, fmt.Errorf("missing label: %q", _auroraMetadataKey)
}
