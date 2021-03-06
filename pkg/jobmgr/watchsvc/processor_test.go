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

package watchsvc

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/uber/peloton/.gen/peloton/api/v1alpha/pod"

	"github.com/stretchr/testify/suite"
	"github.com/uber-go/tally"
	"go.uber.org/yarpc/yarpcerrors"
)

type WatchProcessorTestSuite struct {
	suite.Suite

	ctx       context.Context
	testScope tally.TestScope

	config Config

	processor WatchProcessor
}

func (suite *WatchProcessorTestSuite) SetupTest() {
	suite.ctx = context.Background()
	suite.testScope = tally.NewTestScope("", map[string]string{})

	suite.config = Config{
		BufferSize: 10,
		MaxClient:  2,
	}
	suite.processor = newWatchProcessor(suite.config, suite.testScope)
}

func TestWatchProcessor(t *testing.T) {
	suite.Run(t, &WatchProcessorTestSuite{})
}

// TestInitWatchProcessor tests initialization of WatchProcessor
func (suite *WatchProcessorTestSuite) TestInitWatchProcessor() {
	suite.Nil(GetWatchProcessor())
	InitWatchProcessor(suite.config, suite.testScope)
	suite.NotNil(GetWatchProcessor())
}

// TestTaskClient tests basic setup and teardown of task watch client
func (suite *WatchProcessorTestSuite) TestTaskClient() {
	watchID, c, err := suite.processor.NewTaskClient()
	suite.NoError(err)
	suite.NotEmpty(watchID)
	suite.NotNil(c)

	var wg sync.WaitGroup
	wg.Add(1)
	var stopSignal StopSignal

	go func() {
		defer wg.Done()
		for {
			select {
			case <-c.Input:
			case stopSignal = <-c.Signal:
				return
			}
		}
	}()

	err = suite.processor.StopTaskClient(watchID)
	wg.Wait()

	suite.NoError(err)
	suite.Equal(StopSignalCancel, stopSignal)
}

// TestTaskClient_StopNonexistentClient tests an error will be thrown if
// tearing down a client with unknown watch id.
func (suite *WatchProcessorTestSuite) TestTaskClient_StopNonexistentClient() {
	watchID, c, err := suite.processor.NewTaskClient()
	suite.NoError(err)
	suite.NotEmpty(watchID)
	suite.NotNil(c)

	err = suite.processor.StopTaskClient("00000000-0000-0000-0000-000000000000")
	suite.Error(err)
	suite.True(yarpcerrors.IsNotFound(err))
}

// TestTaskClient_MaxClientReached tests an error will be thrown when
// creating a new client if max number of clients is reached.
func (suite *WatchProcessorTestSuite) TestTaskClient_MaxClientReached() {
	for i := 0; i < 3; i++ {
		watchID, c, err := suite.processor.NewTaskClient()
		if i < 2 {
			suite.NoError(err)
			suite.NotEmpty(watchID)
			suite.NotNil(c)
		} else {
			suite.Error(err)
			suite.True(yarpcerrors.IsResourceExhausted(err))
		}
	}
}

// TestTaskClient_EventOverflow tests that a "overflow" stop Signal will be
// sent to the client and the client will be closed if the client buffer is
// overflown.
func (suite *WatchProcessorTestSuite) TestTaskClient_EventOverflow() {
	watchID, c, err := suite.processor.NewTaskClient()
	suite.NoError(err)
	suite.NotEmpty(watchID)
	suite.NotNil(c)

	var wg sync.WaitGroup
	wg.Add(1)
	var stopSignal StopSignal

	go func() {
		defer wg.Done()
		for {
			select {
			case stopSignal = <-c.Signal:
				return
			}
		}
	}()

	// send number of events equal to buffer size
	for i := 0; i < 10; i++ {
		suite.processor.NotifyTaskChange(&pod.PodSummary{})
	}
	time.Sleep(1 * time.Second)
	suite.Equal(StopSignalUnknown, stopSignal)

	// trigger buffer overflow
	suite.processor.NotifyTaskChange(&pod.PodSummary{})
	wg.Wait()
	suite.Equal(StopSignalOverflow, stopSignal)
}
