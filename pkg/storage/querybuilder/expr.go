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

package querybuilder

import (
	"fmt"
	"io"
	"reflect"
	"strings"

	"database/sql/driver"

	"github.com/gocql/gocql"
)

type expr struct {
	sql  string
	args []interface{}
}

// expr builds value expressions for InsertBuilder and UpdateBuilder.
//
// Ex:
//     .Values(Expr("FROM_UNIXTIME(?)", t))
func expression(sql string, args ...interface{}) expr {
	return expr{sql: sql, args: args}
}

func (e expr) ToSQL() (sql string, args []interface{}, err error) {
	return e.sql, e.args, nil
}

type exprs []expr

func (es exprs) AppendToSQL(w io.Writer, sep string, args []interface{}) ([]interface{}, error) {
	for i, e := range es {
		if i > 0 {
			_, err := io.WriteString(w, sep)
			if err != nil {
				return nil, err
			}
		}
		_, err := io.WriteString(w, e.sql)
		if err != nil {
			return nil, err
		}
		args = append(args, e.args...)
	}
	return args, nil
}

// aliasExpr helps to alias part of SQL query generated with underlying "expr"
type aliasExpr struct {
	expr  Sqlizer
	alias string
}

// Alias allows to define alias for column in SelectBuilder. Useful when column is
// defined as complex expression like IF or CASE
// Ex:
//		.Column(Alias(caseStmt, "case_column"))
func alias(expr Sqlizer, alias string) aliasExpr {
	return aliasExpr{expr, alias}
}

// ToSQL converts to SQL string and args
func (e aliasExpr) ToSQL() (sql string, args []interface{}, err error) {
	sql, args, err = e.expr.ToSQL()
	if err == nil {
		sql = fmt.Sprintf("(%s) AS %s", sql, e.alias)
	}
	return
}

// Eq is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(Eq{"id": 1})
type Eq map[string]interface{}

// UUID represents the cassandra uuid data type
type UUID struct {
	gocql.UUID
}

// ParseUUID creates an UUID object from a string
func ParseUUID(input string) (UUID, error) {
	uuid, err := gocql.ParseUUID(input)
	return UUID{UUID: uuid}, err
}

// IsUUID asserts if a value is of a UUID type
func IsUUID(value interface{}) bool {
	switch value.(type) {
	case UUID:
		return true
	case gocql.UUID:
		return true
	case *UUID:
		return true
	case *gocql.UUID:
		return true
	}
	return false
}

func (eq Eq) toSQL(useNotOpr bool) (sql string, args []interface{}, err error) {
	var (
		exprs    []string
		equalOpr = "="
		inOpr    = "IN"
		nullOpr  = "IS"
	)

	if useNotOpr {
		equalOpr = "<>"
		inOpr = "NOT IN"
		nullOpr = "IS NOT"
	}

	for key, val := range eq {
		expr := ""

		switch v := val.(type) {
		case driver.Valuer:
			if val, err = v.Value(); err != nil {
				return
			}
		}

		if val == nil {
			expr = fmt.Sprintf("%s %s NULL", key, nullOpr)
		} else {
			valVal := reflect.ValueOf(val)
			if !IsUUID(val) && (valVal.Kind() == reflect.Array ||
				valVal.Kind() == reflect.Slice) {
				if valVal.Len() == 0 {
					expr = fmt.Sprintf("%s %s (NULL)", key, inOpr)
					if args == nil {
						args = []interface{}{}
					}
				} else {
					for i := 0; i < valVal.Len(); i++ {
						args = append(args, valVal.Index(i).Interface())
					}
					expr = fmt.Sprintf("%s %s (%s)", key, inOpr, Placeholders(valVal.Len()))
				}
			} else {
				expr = fmt.Sprintf("%s %s ?", key, equalOpr)
				args = append(args, val)
			}
		}
		exprs = append(exprs, expr)
	}
	sql = strings.Join(exprs, " AND ")
	return
}

// ToSQL converts to SQL string and args
func (eq Eq) ToSQL() (sql string, args []interface{}, err error) {
	return eq.toSQL(false)
}

// NotEq is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(NotEq{"id": 1}) == "id <> 1"
type NotEq Eq

// ToSQL converts to SQL string and args
func (neq NotEq) ToSQL() (sql string, args []interface{}, err error) {
	return Eq(neq).toSQL(true)
}

// Lt is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(Lt{"id": 1})
type Lt map[string]interface{}

func (lt Lt) toSQL(opposite, orEq bool) (sql string, args []interface{}, err error) {
	var (
		exprs []string
		opr   = "<"
	)

	if opposite {
		opr = ">"
	}

	if orEq {
		opr = fmt.Sprintf("%s%s", opr, "=")
	}

	for key, val := range lt {
		expr := ""

		switch v := val.(type) {
		case driver.Valuer:
			if val, err = v.Value(); err != nil {
				return
			}
		}
		if val == nil {
			err = fmt.Errorf("cannot use null with less than or greater than operators")
			return
		}
		valVal := reflect.ValueOf(val)
		if valVal.Kind() == reflect.Array || valVal.Kind() == reflect.Slice {
			err = fmt.Errorf("cannot use UUID, array or slice with less than or greater than operators")
			return
		}
		expr = fmt.Sprintf("%s %s ?", key, opr)
		args = append(args, val)
		exprs = append(exprs, expr)
	}
	sql = strings.Join(exprs, " AND ")
	return
}

// ToSQL converts to SQL string and args
func (lt Lt) ToSQL() (sql string, args []interface{}, err error) {
	return lt.toSQL(false, false)
}

// LtOrEq is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(LtOrEq{"id": 1}) == "id <= 1"
type LtOrEq Lt

// ToSQL converts to SQL string and args
func (ltOrEq LtOrEq) ToSQL() (sql string, args []interface{}, err error) {
	return Lt(ltOrEq).toSQL(false, true)
}

// Gt is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(Gt{"id": 1}) == "id > 1"
type Gt Lt

// ToSQL converts to SQL string and args
func (gt Gt) ToSQL() (sql string, args []interface{}, err error) {
	return Lt(gt).toSQL(true, false)
}

// GtOrEq is syntactic sugar for use with Where/Having/Set methods.
// Ex:
//     .Where(GtOrEq{"id": 1}) == "id >= 1"
type GtOrEq Lt

// ToSQL converts to SQL string and args
func (gtOrEq GtOrEq) ToSQL() (sql string, args []interface{}, err error) {
	return Lt(gtOrEq).toSQL(true, true)
}

type conj []Sqlizer

func (c conj) join(sep string) (sql string, args []interface{}, err error) {
	var sqlParts []string
	for _, sqlizer := range c {
		partSQL, partArgs, err := sqlizer.ToSQL()
		if err != nil {
			return "", nil, err
		}
		if partSQL != "" {
			sqlParts = append(sqlParts, partSQL)
			args = append(args, partArgs...)
		}
	}
	if len(sqlParts) > 0 {
		sql = fmt.Sprintf("(%s)", strings.Join(sqlParts, sep))
	}
	return
}

// And is of type conj
type And conj

// ToSQL converts to SQL string and args
func (a And) ToSQL() (string, []interface{}, error) {
	return conj(a).join(" AND ")
}

// Or is of type conj
type Or conj

// ToSQL converts to SQL string and args
func (o Or) ToSQL() (string, []interface{}, error) {
	return conj(o).join(" OR ")
}
